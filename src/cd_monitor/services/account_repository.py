"""AccountRepository: multi-account login state registry (P5.2+).

Each account row maps to its own Playwright storage_state JSON file on disk.
The on-disk path is the source of truth for the cookies/origins payload; the DB
row only stores metadata (name, platform, enabled flag, notes, timestamps).

File layout:
  <ACCOUNT_STATE_DIR>/<platform>/<name>.json

Pattern inspired by Usagi-org/ai-goofish-monitor /api/accounts (named JSON
files in a per-account directory), but adapted to Kuro Atelier stdlib-only
web stack and our existing SQLite conventions.
"""
from __future__ import annotations

import json
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterator, Optional, Union

ACCOUNT_NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,50}$")
PLATFORMS = ("xianyu", "wameiji")
DEFAULT_ACCOUNT_DIR = "data/accounts"


class AccountError(ValueError):
    """Raised on any AccountRepository validation or state error."""


def _validate_name(name):
    if not isinstance(name, str):
        raise AccountError("account name must be a string")
    trimmed = name.strip()
    if not trimmed or not ACCOUNT_NAME_RE.match(trimmed):
        raise AccountError(
            "account name must be 1-50 chars of letters, digits, underscore, or hyphen"
        )
    return trimmed


def _validate_platform(platform):
    if platform not in PLATFORMS:
        raise AccountError("platform must be one of " + str(PLATFORMS))
    return platform


def _row_to_account(row):
    d = dict(row)
    d["enabled"] = bool(d.get("enabled", 0))
    return d


class AccountRepository:
    """CRUD for the accounts table + on-disk state files."""

    def __init__(self, db_path, account_dir=None):
        self.db_path = Path(db_path)
        self.account_dir = Path(account_dir) if account_dir else Path(DEFAULT_ACCOUNT_DIR)

    def _ensure_table(self):
        """Idempotent CREATE TABLE for accounts so the service can be used
        standalone in tests and scripts without going through init_db()."""
        with sqlite3.connect(self.db_path, timeout=10) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS accounts (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  name TEXT NOT NULL,
                  platform TEXT NOT NULL,
                  state_file TEXT NOT NULL,
                  notes TEXT,
                  enabled INTEGER DEFAULT 1,
                  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                  UNIQUE(name, platform)
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_accounts_platform_name ON accounts(platform, name)"
            )
            conn.commit()

    @contextmanager
    def _conn(self):
        self._ensure_table()
        with sqlite3.connect(self.db_path, timeout=10) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            yield conn
            conn.commit()

    def _state_path(self, platform, name):
        return self.account_dir / platform / (name + ".json")

    def ensure_dirs(self, platform):
        d = self.account_dir / platform
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _write_state(self, platform, name, payload):
        path = self._state_path(platform, name)
        self.ensure_dirs(platform)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)
        return path

    def _delete_state(self, platform, name):
        path = self._state_path(platform, name)
        if path.exists():
            try:
                path.unlink()
                return True
            except OSError:
                return False
        return False

    def list_all(self, platform=None):
        with self._conn() as conn:
            if platform:
                rows = conn.execute(
                    "SELECT * FROM accounts WHERE platform = ? ORDER BY name ASC",
                    (platform,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM accounts ORDER BY platform ASC, name ASC"
                ).fetchall()
        return [_row_to_account(r) for r in rows]

    def get(self, account_id):
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM accounts WHERE id = ?", (account_id,)).fetchone()
        return _row_to_account(row) if row else None

    def find(self, platform, name):
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM accounts WHERE platform = ? AND name = ?",
                (platform, name),
            ).fetchone()
        return _row_to_account(row) if row else None

    def create(self, name, platform, state_payload, notes=None, enabled=True):
        name = _validate_name(name)
        platform = _validate_platform(platform)
        if not isinstance(state_payload, dict):
            raise AccountError("state_payload must be a JSON object")
        if self.find(platform, name) is not None:
            raise AccountError("account already exists: " + platform + "/" + name)
        state_file = self._write_state(platform, name, state_payload)
        try:
            with self._conn() as conn:
                cur = conn.execute(
                    "INSERT INTO accounts (name, platform, state_file, notes, enabled)"
                    " VALUES (?, ?, ?, ?, ?)",
                    (name, platform, str(state_file), notes, 1 if enabled else 0),
                )
                account_id = int(cur.lastrowid or 0)
        except sqlite3.IntegrityError as exc:
            self._delete_state(platform, name)
            raise AccountError("account creation failed: " + str(exc)) from exc
        return self.get(account_id)

    def update_content(self, account_id, state_payload):
        if not isinstance(state_payload, dict):
            raise AccountError("state_payload must be a JSON object")
        account = self.get(account_id)
        if account is None:
            return None
        self._write_state(account["platform"], account["name"], state_payload)
        with self._conn() as conn:
            conn.execute(
                "UPDATE accounts SET updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (account_id,),
            )
        return self.get(account_id)

    def update_meta(self, account_id, notes=None, enabled=None):
        sets, args = [], []
        if notes is not None:
            sets.append("notes = ?")
            args.append(notes)
        if enabled is not None:
            sets.append("enabled = ?")
            args.append(1 if enabled else 0)
        if not sets:
            return self.get(account_id)
        sets.append("updated_at = CURRENT_TIMESTAMP")
        args.append(account_id)
        with self._conn() as conn:
            conn.execute(
                "UPDATE accounts SET " + ", ".join(sets) + " WHERE id = ?",
                args,
            )
        return self.get(account_id)

    def delete(self, account_id):
        account = self.get(account_id)
        if account is None:
            return False
        self._delete_state(account["platform"], account["name"])
        with self._conn() as conn:
            cur = conn.execute("DELETE FROM accounts WHERE id = ?", (account_id,))
        return cur.rowcount > 0

    def read_state(self, account_id):
        account = self.get(account_id)
        if account is None:
            return None
        path = Path(account["state_file"])
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

    def state_status(self, account_id):
        account = self.get(account_id)
        if account is None:
            return {"status": "not_found"}
        path = Path(account["state_file"])
        if not path.exists():
            return {
                "status": "missing",
                "error_type": "state_file_missing",
                "error_message": "Account state file does not exist.",
            }
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            return {
                "status": "invalid",
                "error_type": "invalid_state_file",
                "error_message": str(exc),
            }
        if not isinstance(data, dict):
            return {
                "status": "invalid",
                "error_type": "invalid_state_file",
                "error_message": "state file must be a JSON object",
            }
        return {"status": "ready"}


__all__ = [
    "AccountError",
    "AccountRepository",
    "ACCOUNT_NAME_RE",
    "DEFAULT_ACCOUNT_DIR",
    "PLATFORMS",
]