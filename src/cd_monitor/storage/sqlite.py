from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from cd_monitor.core.identifiers import normalize_catalog_no_compact
from cd_monitor.core.models import MarketItem, Opportunity, WatchItem, XianyuPriceSample
from cd_monitor.storage.migrations import SCHEMA_SQL



def _migrate_watchlist_settings(conn: sqlite3.Connection) -> None:
    """Idempotently add task-settings columns to legacy watchlist tables.

    Pre-P5.1 fields: min_margin, min_diff, notify_channel, platform.
    P5.1+ fields: cron, ai_prompt_base_file, ai_prompt_criteria_file,
    failure_count, paused_until, last_status, last_run_at, next_run_at.
    """
    existing = {row[1] for row in conn.execute("PRAGMA table_info(watchlist)").fetchall()}
    additions: list[tuple[str, str]] = []
    if "min_margin" not in existing:
        additions.append(("min_margin", "REAL DEFAULT 0.30"))
    if "min_diff" not in existing:
        additions.append(("min_diff", "REAL DEFAULT 1500"))
    if "notify_channel" not in existing:
        additions.append(("notify_channel", "TEXT DEFAULT 'none'"))
    if "platform" not in existing:
        additions.append(("platform", "TEXT DEFAULT 'both'"))
    if "cron" not in existing:
        additions.append(("cron", "TEXT"))
    if "ai_prompt_base_file" not in existing:
        additions.append(("ai_prompt_base_file", "TEXT"))
    if "ai_prompt_criteria_file" not in existing:
        additions.append(("ai_prompt_criteria_file", "TEXT"))
    if "failure_count" not in existing:
        additions.append(("failure_count", "INTEGER DEFAULT 0"))
    if "paused_until" not in existing:
        additions.append(("paused_until", "TIMESTAMP"))
    if "is_running" not in existing:
        additions.append(("is_running", "INTEGER DEFAULT 0"))
    if "last_status" not in existing:
        additions.append(("last_status", "TEXT"))
    if "last_run_at" not in existing:
        additions.append(("last_run_at", "TIMESTAMP"))
    if "next_run_at" not in existing:
        additions.append(("next_run_at", "TIMESTAMP"))
    # P5.3: task-level account binding (mirrors Usagi ai-goofish-monitor).
    # account_state_file: <platform>/<name>.json relative path
    #   (or empty when strategy != fixed).
    # account_strategy: auto | fixed | rotate.
    if "account_state_file" not in existing:
        additions.append(("account_state_file", "TEXT"))
    if "account_strategy" not in existing:
        additions.append(("account_strategy", "TEXT DEFAULT " + chr(39) + "auto" + chr(39)))
    # P5.4: AI/keyword mode + description (mirrors Usagi domain/models/task.py).
    # decision_mode: ai | keyword. ai requires description; keyword uses keyword_rules.
    # description: free-text user requirement (only enforced when ai).
    if "decision_mode" not in existing:
        additions.append(("decision_mode", "TEXT DEFAULT " + chr(39) + "ai" + chr(39)))
    if "description" not in existing:
        additions.append(("description", "TEXT"))
    if "match_mode" not in existing:
        additions.append(("match_mode", "TEXT DEFAULT 'any'"))
    for col, decl in additions:
        conn.execute(f"ALTER TABLE watchlist ADD COLUMN {col} {decl}")


def _migrate_user_settings(conn: sqlite3.Connection) -> None:
    """Create user_settings table for persistent UI preferences (overrides)."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS user_settings (
          key TEXT PRIMARY KEY,
          value TEXT NOT NULL,
          updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )


def _migrate_price_snapshots(conn: sqlite3.Connection) -> None:
    """Per-scan price snapshots. P5.1+ with P5.5 per-catalog semantics.

    Kuro extends the table with three columns so each snapshot can be
    traced back to one full scan pass:

    * ``source_kind``  ? mock | live | manual (who produced the price).
    * ``scan_run_id``  ? UUID string shared by every snapshot that
                         belongs to a single scan pass.
    * ``notes``        ? optional free text the scan pipeline can
                         attach to the row (e.g. an AI decision
                         summary).

    Indexes cover the two access patterns the Web UI hits: per-catalog
    timeline and per-scan retrieval.
    """
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS price_snapshots (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          catalog_no TEXT NOT NULL,
          source TEXT NOT NULL,
          platform TEXT NOT NULL,
          price REAL NOT NULL,
          currency TEXT NOT NULL,
          price_cny REAL,
          sample_count INTEGER DEFAULT 0,
          decision TEXT,
          opportunity_id INTEGER,
          task_id INTEGER,
          run_id INTEGER,
          source_kind TEXT DEFAULT 'mock',
          scan_run_id TEXT,
          notes TEXT,
          captured_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    existing = {row[1] for row in conn.execute("PRAGMA table_info(price_snapshots)").fetchall()}
    additions: list[tuple[str, str]] = []
    if "source_kind" not in existing:
        additions.append(("source_kind", "TEXT DEFAULT 'mock'"))
    if "scan_run_id" not in existing:
        additions.append(("scan_run_id", "TEXT"))
    if "notes" not in existing:
        additions.append(("notes", "TEXT"))
    for col, decl in additions:
        conn.execute(f"ALTER TABLE price_snapshots ADD COLUMN {col} {decl}")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_price_snapshots_catalog_time"
        " ON price_snapshots(catalog_no, captured_at DESC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_price_snapshots_scan_run"
        " ON price_snapshots(scan_run_id)"
    )


def _migrate_failure_records(conn: sqlite3.Connection) -> None:
    """Failure log + auto-pause history per task. P5.1+."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS failure_records (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          task_id INTEGER,
          task_name TEXT,
          source TEXT,
          keyword TEXT,
          error_type TEXT,
          error_message TEXT,
          paused INTEGER DEFAULT 0,
          notified_at TIMESTAMP,
          created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_failure_records_task_time"
        " ON failure_records(task_id, created_at DESC)"
    )

def _migrate_result_blacklist_rules(conn: sqlite3.Connection) -> None:
    """Per-watchlist result blacklist keyword rules (P5.5+).

    Mirrors Usagi's `result_blacklist_rules` table but uses watch_id (the
    `watchlist.id` of the owning task) as the key, so each watchlist has
    its own keyword blacklist. Replaces the legacy task-exclusion logic
    that lived inside the keyword rule engine.
    """
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS result_blacklist_rules (
          watch_id INTEGER PRIMARY KEY,
          blacklist_keywords_json TEXT NOT NULL DEFAULT '[]',
          updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
          FOREIGN KEY(watch_id) REFERENCES watchlist(id) ON DELETE CASCADE
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_result_blacklist_rules_updated"
        " ON result_blacklist_rules(updated_at DESC)"
    )


def _migrate_accounts(conn: sqlite3.Connection) -> None:
    """Multi-account login state registry (P5.2+).

    Each account maps to its own JSON state file under
    <ACCOUNT_STATE_DIR>/<platform>/<name>.json. Tasks may reference an
    account by id (via watchlist.account_id) so a single scan job can use
    any of the configured login states.
    """
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



def _migrate_opportunity_p55_columns(conn: sqlite3.Connection) -> None:
    """P5.5: per-catalog / per-scan parity columns from Usagi.

    Adopts the columns Usagi tracks in ``opportunities`` so the Web UI
    can distinguish AI vs keyword recommendations and per-keyword
    blacklist hits without computing it on every request.

    * ``analysis_source``  — ai | keyword (what produced the decision).
    * ``status``            — active | manual | expired (matches Usagi).
    * ``link_unique_key``   — URL with query string stripped (deduplication
                             anchor across re-imports of the same product).
    * ``seller_nickname``   — xianyu seller handle, surfaced for filtering.
    * ``publish_time``      — wameiji publish_time, surfaced for "new" only.
    * ``xianyu_display_sample_id`` — explicit market_items reference for display.
    """
    existing = {row[1] for row in conn.execute("PRAGMA table_info(opportunities)").fetchall()}
    additions: list[tuple[str, str]] = []
    if "analysis_source" not in existing:
        additions.append(("analysis_source", "TEXT DEFAULT 'ai'"))
    if "status" not in existing:
        additions.append(("status", "TEXT DEFAULT 'active'"))
    if "link_unique_key" not in existing:
        additions.append(("link_unique_key", "TEXT"))
    if "seller_nickname" not in existing:
        additions.append(("seller_nickname", "TEXT"))
    if "publish_time" not in existing:
        additions.append(("publish_time", "TEXT"))
    if "xianyu_display_sample_id" not in existing:
        additions.append(("xianyu_display_sample_id", "INTEGER"))
    for col, decl in additions:
        conn.execute(f"ALTER TABLE opportunities ADD COLUMN {col} {decl}")
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_opportunities_catalog_link"
        " ON opportunities(catalog_no, link_unique_key)"
        " WHERE link_unique_key IS NOT NULL"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_opportunities_status"
        " ON opportunities(status)"
    )


def _migrate_watchlist_filter_columns(conn: sqlite3.Connection) -> None:
    """P5.5: scan knobs the Web UI filters on.

    Mirrors the Usagi ``tasks`` filter columns:
    ``region``, ``personal_only``, ``analyze_images``, ``min_price`` /
    ``max_price`` (in CNY), ``max_pages`` (search depth limit).
    Defaults are permissive so existing rows keep producing the same
    recommendations.
    """
    existing = {row[1] for row in conn.execute("PRAGMA table_info(watchlist)").fetchall()}
    additions: list[tuple[str, str]] = []
    if "region" not in existing:
        additions.append(("region", "TEXT DEFAULT 'cn'"))
    if "personal_only" not in existing:
        additions.append(("personal_only", "INTEGER DEFAULT 0"))
    if "analyze_images" not in existing:
        additions.append(("analyze_images", "INTEGER DEFAULT 1"))
    if "min_price" not in existing:
        additions.append(("min_price", "REAL"))
    if "max_price" not in existing:
        additions.append(("max_price", "REAL"))
    if "max_pages" not in existing:
        additions.append(("max_pages", "INTEGER DEFAULT 5"))
    for col, decl in additions:
        conn.execute(f"ALTER TABLE watchlist ADD COLUMN {col} {decl}")





def reset_all_is_running(db_path: str | Path = "data/cd_monitor.db") -> int:
    """Set is_running=0 on every watchlist row. Called at server startup
    so a crashed prior process leaves the database in a recoverable
    state (Usagi mirrors this with `process_service.stop_all()` in its
    lifespan shutdown hook; Kuro keeps it idempotent + synchronous)."""
    init_db(db_path)
    with sqlite3.connect(db_path) as conn:
        cur = conn.execute("UPDATE watchlist SET is_running = 0, last_status = COALESCE(last_status, 'reset_on_boot')")
        conn.commit()
        return int(cur.rowcount or 0)




def _migrate_market_items_cover_columns(conn: sqlite3.Connection) -> None:
    """Add cover_text, image_phash, image_dhash if missing.

    Required by data/match_real.py cover-text / pHash matching paths.
    Uses IF NOT EXISTS-style guard via PRAGMA table_info.
    """
    cols = {row[1] for row in conn.execute("PRAGMA table_info(market_items)").fetchall()}
    if "cover_text" not in cols:
        try:
            conn.execute('ALTER TABLE market_items ADD COLUMN cover_text TEXT DEFAULT ""')
        except Exception:
            pass
    if "image_phash" not in cols:
        try:
            conn.execute('ALTER TABLE market_items ADD COLUMN image_phash TEXT DEFAULT ""')
        except Exception:
            pass
    if "image_dhash" not in cols:
        try:
            conn.execute('ALTER TABLE market_items ADD COLUMN image_dhash TEXT DEFAULT ""')
        except Exception:
            pass


def init_db(db_path: str | Path = "data/cd_monitor.db") -> None:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    try:
        with conn:
            conn.executescript(SCHEMA_SQL)
            _migrate_candidate_rechecks_pending_unique(conn)
            _migrate_watchlist_settings(conn)
            _migrate_user_settings(conn)
            _migrate_price_snapshots(conn)
            _migrate_failure_records(conn)
            _migrate_accounts(conn)
            _migrate_result_blacklist_rules(conn)
            _migrate_opportunity_p55_columns(conn)
            _migrate_watchlist_filter_columns(conn)
            _migrate_market_items_cover_columns(conn)
    finally:
        conn.close()


def _migrate_candidate_rechecks_pending_unique(conn: sqlite3.Connection) -> None:
    if not _has_legacy_candidate_rechecks_unique_constraint(conn):
        return
    conn.execute("ALTER TABLE candidate_rechecks RENAME TO candidate_rechecks_legacy")
    conn.execute(
        """
        CREATE TABLE candidate_rechecks (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          opportunity_id INTEGER NOT NULL,
          status TEXT NOT NULL DEFAULT 'pending',
          reason TEXT,
          scheduled_at TEXT NOT NULL,
          created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
          updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
          FOREIGN KEY(opportunity_id) REFERENCES opportunities(id)
        )
        """
    )
    conn.execute(
        """
        INSERT INTO candidate_rechecks (
          id, opportunity_id, status, reason, scheduled_at, created_at, updated_at
        )
        SELECT id, opportunity_id, status, reason, scheduled_at, created_at, updated_at
        FROM candidate_rechecks_legacy
        """
    )
    conn.execute("DROP TABLE candidate_rechecks_legacy")
    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_candidate_rechecks_one_pending
        ON candidate_rechecks(opportunity_id)
        WHERE status = 'pending'
        """
    )


def _has_legacy_candidate_rechecks_unique_constraint(conn: sqlite3.Connection) -> bool:
    indexes = conn.execute("PRAGMA index_list(candidate_rechecks)").fetchall()
    for index in indexes:
        name = str(index[1])
        is_unique = bool(index[2])
        is_partial = bool(index[4]) if len(index) > 4 else False
        if not is_unique or is_partial:
            continue
        columns = [
            str(row[2])
            for row in conn.execute(f"PRAGMA index_info({name})").fetchall()
        ]
        if columns == ["opportunity_id", "status"]:
            return True
    return False


def insert_search_run(
    db_path: str | Path,
    source: str,
    keyword: str,
    status: str,
    error_type: str | None = None,
    error_message: str | None = None,
    screenshot_path: str | None = None,
    raw_snapshot_path: str | None = None,
) -> int:
    init_db(db_path)
    with sqlite3.connect(db_path) as conn:
        cur = conn.execute(
            """
            INSERT INTO search_runs (source, keyword, status, error_type, error_message,
              screenshot_path, raw_snapshot_path)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (source, keyword, status, error_type, error_message, screenshot_path, raw_snapshot_path),
        )
        return int(cur.lastrowid)


def finish_search_run(
    db_path: str | Path,
    search_run_id: int,
    status: str,
    error_type: str | None = None,
    error_message: str | None = None,
    screenshot_path: str | None = None,
    raw_snapshot_path: str | None = None,
) -> None:
    init_db(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            UPDATE search_runs
            SET status = ?, finished_at = CURRENT_TIMESTAMP, error_type = COALESCE(?, error_type),
              error_message = COALESCE(?, error_message), screenshot_path = COALESCE(?, screenshot_path),
              raw_snapshot_path = COALESCE(?, raw_snapshot_path)
            WHERE id = ?
            """,
            (status, error_type, error_message, screenshot_path, raw_snapshot_path, search_run_id),
        )


def add_watch(db_path: str | Path, watch: WatchItem) -> int:
    init_db(db_path)
    with sqlite3.connect(db_path) as conn:
        cur = conn.execute(
            """
            INSERT INTO watchlist (catalog_no, catalog_no_compact, jan, artist, title_jp, title_cn,
              edition, required_keywords, excluded_keywords, priority, expected_holding_days,
              min_margin, min_diff, notify_channel, platform,
              account_state_file, account_strategy,
              decision_mode, description, ai_prompt_base_file, ai_prompt_criteria_file,
              region, personal_only, analyze_images, min_price, max_price, max_pages)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?,
                    ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?)
            """,
            (
                watch.catalog_no,
                normalize_catalog_no_compact(watch.catalog_no),
                watch.jan,
                watch.artist,
                watch.title_jp,
                watch.title_cn,
                watch.edition,
                json.dumps(watch.required_keywords, ensure_ascii=False),
                json.dumps(watch.excluded_keywords, ensure_ascii=False),
                watch.priority,
                watch.expected_holding_days,
                watch.min_margin,
                watch.min_diff,
                watch.notify_channel,
                watch.platform,
                watch.account_state_file,
                watch.account_strategy,
                watch.decision_mode,
                watch.description,
                watch.ai_prompt_base_file,
                watch.ai_prompt_criteria_file,
                watch.region,
                1 if watch.personal_only else 0,
                1 if watch.analyze_images else 0,
                watch.min_price,
                watch.max_price,
                watch.max_pages,
            ),
        )
        return int(cur.lastrowid)


def list_watch(db_path: str | Path) -> list[WatchItem]:
    init_db(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM watchlist WHERE enabled = 1 ORDER BY id").fetchall()
    return [
        WatchItem(
            catalog_no=row["catalog_no"],
            jan=row["jan"],
            artist=row["artist"],
            title_jp=row["title_jp"],
            title_cn=row["title_cn"],
            edition=row["edition"],
            required_keywords=json.loads(row["required_keywords"] or "[]"),
            excluded_keywords=json.loads(row["excluded_keywords"] or "[]"),
            priority=row["priority"] if row["priority"] is not None else 1,
            expected_holding_days=row["expected_holding_days"] if row["expected_holding_days"] is not None else 30,
            min_margin=row["min_margin"] if row["min_margin"] is not None else 0.30,
            min_diff=row["min_diff"] if row["min_diff"] is not None else 1500.0,
            notify_channel=row["notify_channel"] or "none",
            platform=row["platform"] or "both",
            enabled=bool(row["enabled"]) if "enabled" in row.keys() else True,
            account_state_file=row["account_state_file"] if "account_state_file" in row.keys() else None,
            account_strategy=row["account_strategy"] if "account_strategy" in row.keys() else "auto",
            decision_mode=row["decision_mode"] if "decision_mode" in row.keys() and row["decision_mode"] else "ai",
            description=row["description"] if "description" in row.keys() else None,
            ai_prompt_base_file=row["ai_prompt_base_file"] if "ai_prompt_base_file" in row.keys() else None,
            ai_prompt_criteria_file=row["ai_prompt_criteria_file"] if "ai_prompt_criteria_file" in row.keys() else None,
            region=row["region"] if "region" in row.keys() else None,
            personal_only=bool(row["personal_only"]) if "personal_only" in row.keys() else False,
            analyze_images=bool(row["analyze_images"]) if "analyze_images" in row.keys() else True,
            min_price=row["min_price"] if "min_price" in row.keys() else None,
            max_price=row["max_price"] if "max_price" in row.keys() else None,
            max_pages=int(row["max_pages"]) if "max_pages" in row.keys() and row["max_pages"] is not None else 5,
        )
        for row in rows
    ]


def update_watch(db_path: str | Path, watch_id: int, updates: dict[str, object]) -> bool:
    allowed_fields = {
        "jan",
        "artist",
        "title_jp",
        "title_cn",
        "edition",
        "required_keywords",
        "excluded_keywords",
        "priority",
        "expected_holding_days",
        "min_margin",
        "min_diff",
        "notify_channel",
        "platform",
        # P5.3: task-level account binding.
        "account_state_file",
        "account_strategy",
        # P5.4: AI/keyword mode + per-task AI prompt.
        "decision_mode",
        "description",
        "ai_prompt_base_file",
        "ai_prompt_criteria_file",
        # P5.5 scan filters (Usagi parity).
        "region",
        "personal_only",
        "analyze_images",
        "min_price",
        "max_price",
        "max_pages",
        "is_running",
    }
    assignments: list[str] = []
    values: list[object] = []
    for field, value in updates.items():
        if field not in allowed_fields:
            continue
        assignments.append(f"{field} = ?")
        if field in {"required_keywords", "excluded_keywords"}:
            value = json.dumps(value or [], ensure_ascii=False)
        values.append(value)
    if not assignments:
        return False
    assignments.append("updated_at = CURRENT_TIMESTAMP")
    values.append(watch_id)
    init_db(db_path)
    with sqlite3.connect(db_path) as conn:
        cur = conn.execute(
            f"UPDATE watchlist SET {', '.join(assignments)} WHERE id = ? AND enabled = 1",
            tuple(values),
        )
        return cur.rowcount > 0


def disable_watch(db_path: str | Path, watch_id: int) -> bool:
    init_db(db_path)
    with sqlite3.connect(db_path) as conn:
        cur = conn.execute(
            """
            UPDATE watchlist
            SET enabled = 0, updated_at = CURRENT_TIMESTAMP
            WHERE id = ? AND enabled = 1
            """,
            (watch_id,),
        )
        return cur.rowcount > 0

def enable_watch(db_path: str | Path, watch_id: int) -> bool:
    init_db(db_path)
    with sqlite3.connect(db_path) as conn:
        cur = conn.execute(
            """
            UPDATE watchlist
            SET enabled = 1, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (watch_id,),
        )
        return cur.rowcount > 0



def fetch_watch_min(db_path: str | Path, watch_id: int) -> dict[str, Any] | None:
    """Fetch minimum columns needed by cascade-delete.

    Returns a dict with id and catalog_no (used to decide whether other
    watches still pin the catalog) or None if the row no longer exists.
    """
    init_db(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT id, catalog_no FROM watchlist WHERE id = ?",
            (watch_id,),
        ).fetchone()
    if row is None:
        return None
    return {"id": int(row["id"]), "catalog_no": row["catalog_no"]}


def count_watch_using_catalog(
    db_path: str | Path,
    catalog_no: str,
    exclude_watch_id: int | None = None,
) -> int:
    """Count watchlist rows that still reference catalog_no.

    Pass exclude_watch_id (the watch being deleted) so we can decide whether
    the catalog is now orphaned and a cascade (opportunities, price
    snapshots) is safe.
    """
    init_db(db_path)
    with sqlite3.connect(db_path) as conn:
        if exclude_watch_id is None:
            row = conn.execute(
                "SELECT COUNT(*) FROM watchlist WHERE catalog_no = ?",
                (catalog_no,),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT COUNT(*) FROM watchlist WHERE catalog_no = ? AND id <> ?",
                (catalog_no, exclude_watch_id),
            ).fetchone()
    return int(row[0]) if row else 0


def delete_opportunities_for_catalog(db_path: str | Path, catalog_no: str) -> int:
    """Cascade-delete every opportunity row tied to catalog_no.

    Returns the number of rows removed. Best-effort: callers should already
    have confirmed no other watch still uses catalog_no.
    """
    init_db(db_path)
    with sqlite3.connect(db_path) as conn:
        cur = conn.execute("DELETE FROM opportunities WHERE catalog_no = ?", (catalog_no,))
        return cur.rowcount


def delete_price_snapshots_for_catalog(db_path: str | Path, catalog_no: str) -> int:
    """Cascade-delete every historical price snapshot tied to catalog_no."""
    init_db(db_path)
    with sqlite3.connect(db_path) as conn:
        cur = conn.execute(
            "DELETE FROM price_snapshots WHERE catalog_no = ?",
            (catalog_no,),
        )
        return cur.rowcount


def delete_watch(db_path: str | Path, watch_id: int) -> bool:
    init_db(db_path)
    with sqlite3.connect(db_path) as conn:
        cur = conn.execute("DELETE FROM watchlist WHERE id = ?", (watch_id,))
        return cur.rowcount > 0


def list_watch_all(db_path: str | Path) -> list[WatchItem]:
    """List all watchlist rows including disabled (for admin/UI)."""
    init_db(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM watchlist ORDER BY id").fetchall()
    return [
        WatchItem(
            catalog_no=row["catalog_no"],
            jan=row["jan"],
            artist=row["artist"],
            title_jp=row["title_jp"],
            title_cn=row["title_cn"],
            edition=row["edition"],
            required_keywords=json.loads(row["required_keywords"] or "[]"),
            excluded_keywords=json.loads(row["excluded_keywords"] or "[]"),
            priority=row["priority"] if row["priority"] is not None else 1,
            expected_holding_days=row["expected_holding_days"] if row["expected_holding_days"] is not None else 30,
            min_margin=row["min_margin"] if row["min_margin"] is not None else 0.30,
            min_diff=row["min_diff"] if row["min_diff"] is not None else 1500.0,
            notify_channel=row["notify_channel"] or "none",
            platform=row["platform"] or "both",
            enabled=bool(row["enabled"]) if "enabled" in row.keys() else True,
            account_state_file=row["account_state_file"] if "account_state_file" in row.keys() else None,
            account_strategy=row["account_strategy"] if "account_strategy" in row.keys() else "auto",
            decision_mode=row["decision_mode"] if "decision_mode" in row.keys() and row["decision_mode"] else "ai",
            description=row["description"] if "description" in row.keys() else None,
            ai_prompt_base_file=row["ai_prompt_base_file"] if "ai_prompt_base_file" in row.keys() else None,
            ai_prompt_criteria_file=row["ai_prompt_criteria_file"] if "ai_prompt_criteria_file" in row.keys() else None,
        )
        for row in rows
    ]


# ----- User settings (UI overrides) -----

def get_user_settings(db_path: str | Path) -> dict[str, str]:
    init_db(db_path)
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute("SELECT key, value FROM user_settings").fetchall()
    return {str(k): str(v) for k, v in rows}


def set_user_settings(db_path: str | Path, settings: dict[str, str]) -> int:
    init_db(db_path)
    count = 0
    with sqlite3.connect(db_path) as conn:
        for k, v in settings.items():
            conn.execute(
                """INSERT INTO user_settings (key, value, updated_at)
                   VALUES (?, ?, CURRENT_TIMESTAMP)
                   ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = CURRENT_TIMESTAMP""",
                (str(k), str(v)),
            )
            count += 1
    return count


def search_opportunities(db_path: str | Path, query: str, limit: int = 50) -> list[dict[str, object]]:
    """Search opportunities by catalog_no / title / artist substring."""
    init_db(db_path)
    q = (query or "").strip()
    if not q:
        return []
    pattern = f"%{q}%"
    sql = (
        "SELECT o.id, o.catalog_no, o.expected_profit, o.net_margin, o.match_confidence, "
        "o.liquidity_status, o.decision, o.created_at, mi.title AS item_title, "
        "mi.price AS purchase_price_jpy, mi.url AS url, mi.image_url "
        "FROM opportunities o "
        "LEFT JOIN market_items mi ON mi.id = o.wameiji_item_id "
        "WHERE o.catalog_no LIKE ? OR mi.title LIKE ? "
        "ORDER BY o.created_at DESC LIMIT ?"
    )
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(sql, (pattern, pattern, int(limit))).fetchall()
    return [dict(row) for row in rows]


def insert_market_items(db_path: str | Path, items: Iterable[MarketItem]) -> list[int]:
    init_db(db_path)
    ids: list[int] = []
    with sqlite3.connect(db_path) as conn:
        for item in items:
            cur = conn.execute(
                """
                INSERT INTO market_items (source, source_site, external_item_id, catalog_no, jan, title,
                  price, currency, price_cny_display, url, image_url, availability, condition_text, raw_text)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item.source,
                    item.source_site,
                    item.external_item_id,
                    item.catalog_no,
                    item.jan,
                    item.title,
                    item.price,
                    item.currency,
                    item.price_cny_display,
                    item.url,
                    item.image_url,
                    item.availability,
                    item.condition_text,
                    item.raw_text,
                ),
            )
            ids.append(int(cur.lastrowid))
    return ids


def insert_xianyu_samples(db_path: str | Path, samples: Iterable[XianyuPriceSample]) -> list[int]:
    init_db(db_path)
    ids: list[int] = []
    with sqlite3.connect(db_path) as conn:
        for sample in samples:
            cur = conn.execute(
                """
                INSERT INTO xianyu_price_samples (catalog_no, title, price_cny, url, image_url,
                  seller_text, raw_text, is_valid, invalid_reason)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    sample.catalog_no,
                    sample.title,
                    sample.price_cny,
                    sample.url,
                    sample.image_url,
                    sample.seller_text,
                    sample.raw_text,
                    int(sample.is_valid),
                    sample.invalid_reason,
                ),
            )
            ids.append(int(cur.lastrowid))
    return ids


def insert_opportunity(db_path: str | Path, opportunity: Opportunity, wameiji_item_id: int | None = None) -> int:
    init_db(db_path)
    with sqlite3.connect(db_path) as conn:
        cur = conn.execute(
            """
            INSERT OR IGNORE INTO opportunities (catalog_no, wameiji_item_id, xianyu_reference_price,
              expected_sale_price, landed_cost, expected_revenue, expected_profit, net_margin,
              turnover_adjusted_roi, match_confidence, valid_xianyu_sample_count, liquidity_status,
              decision, risk_labels, opportunity_hash)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                opportunity.catalog_no,
                wameiji_item_id,
                opportunity.xianyu_reference_price,
                opportunity.expected_sale_price,
                opportunity.landed_cost,
                opportunity.expected_revenue,
                opportunity.expected_profit,
                opportunity.net_margin,
                opportunity.turnover_adjusted_roi,
                opportunity.match_confidence,
                opportunity.valid_xianyu_sample_count,
                opportunity.liquidity_status,
                opportunity.decision,
                json.dumps(opportunity.risk_labels, ensure_ascii=False),
                opportunity.opportunity_hash,
            ),
        )
        if cur.lastrowid:
            return int(cur.lastrowid)
        if opportunity.opportunity_hash:
            existing = conn.execute(
                "SELECT id FROM opportunities WHERE opportunity_hash = ?",
                (opportunity.opportunity_hash,),
            ).fetchone()
            if existing is not None:
                return int(existing[0])
        raise RuntimeError("Opportunity insert was ignored but no existing id could be resolved")


def get_opportunity(db_path: str | Path, opportunity_id: int) -> Opportunity:
    init_db(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """
            SELECT
              o.*,
              m.source, m.source_site, m.external_item_id, m.catalog_no AS item_catalog_no,
              m.jan, m.title, m.price, m.currency, m.price_cny_display, m.url, m.image_url,
              m.availability, m.condition_text, m.raw_text
            FROM opportunities o
            LEFT JOIN market_items m ON m.id = o.wameiji_item_id
            WHERE o.id = ?
            """,
            (opportunity_id,),
        ).fetchone()
    if row is None:
        raise KeyError(f"Opportunity not found: {opportunity_id}")
    item = MarketItem(
        source=row["source"] or "wameiji",
        source_site=row["source_site"],
        external_item_id=row["external_item_id"],
        catalog_no=row["item_catalog_no"],
        jan=row["jan"],
        title=row["title"] or "",
        price=row["price"] or 0,
        currency=row["currency"] or "JPY",
        price_cny_display=row["price_cny_display"],
        url=row["url"],
        image_url=row["image_url"],
        availability=row["availability"] or "unknown_but_visible",
        condition_text=row["condition_text"],
        raw_text=row["raw_text"],
    )
    return Opportunity(
        catalog_no=row["catalog_no"],
        item=item,
        xianyu_reference_price=row["xianyu_reference_price"],
        expected_sale_price=row["expected_sale_price"],
        landed_cost=row["landed_cost"],
        expected_revenue=row["expected_revenue"],
        expected_profit=row["expected_profit"],
        net_margin=row["net_margin"],
        turnover_adjusted_roi=row["turnover_adjusted_roi"],
        match_confidence=row["match_confidence"],
        valid_xianyu_sample_count=row["valid_xianyu_sample_count"],
        liquidity_status=row["liquidity_status"],
        decision=row["decision"],
        risk_labels=json.loads(row["risk_labels"] or "[]"),
        opportunity_hash=row["opportunity_hash"],
    )


def list_opportunities(db_path: str | Path, limit: int = 50) -> list[Opportunity]:
    ids = list_opportunity_ids(db_path, limit)
    return [get_opportunity(db_path, opportunity_id) for opportunity_id in ids]


def list_opportunity_ids(db_path: str | Path, limit: int = 50) -> list[int]:
    init_db(db_path)
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT id FROM opportunities ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [int(row[0]) for row in rows]


def list_market_items(db_path: str | Path, catalog_no: str | None = None) -> list[MarketItem]:
    init_db(db_path)
    sql = """
        SELECT source, source_site, external_item_id, catalog_no, jan, title, price, currency,
          price_cny_display, url, image_url, availability, condition_text, raw_text
        FROM market_items
    """
    params: tuple[object, ...] = ()
    if catalog_no:
        sql += " WHERE catalog_no = ? OR title LIKE ? OR raw_text LIKE ?"
        like = f"%{catalog_no}%"
        params = (catalog_no, like, like)
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(sql, params).fetchall()
    return [
        MarketItem(
            source=row["source"],
            source_site=row["source_site"],
            external_item_id=row["external_item_id"],
            catalog_no=row["catalog_no"],
            jan=row["jan"],
            title=row["title"],
            price=row["price"],
            currency=row["currency"],
            price_cny_display=row["price_cny_display"],
            url=row["url"],
            image_url=row["image_url"],
            availability=row["availability"] or "unknown_but_visible",
            condition_text=row["condition_text"],
            raw_text=row["raw_text"],
        )
        for row in rows
    ]


def list_xianyu_samples(db_path: str | Path, catalog_no: str) -> list[XianyuPriceSample]:
    init_db(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT catalog_no, title, price_cny, url, image_url, seller_text, raw_text,
              is_valid, invalid_reason
            FROM xianyu_price_samples
            WHERE catalog_no = ?
            """,
            (catalog_no,),
        ).fetchall()
    return [
        XianyuPriceSample(
            catalog_no=row["catalog_no"],
            title=row["title"],
            price_cny=row["price_cny"],
            url=row["url"],
            image_url=row["image_url"],
            image_phash=row["image_phash"] if "image_phash" in row.keys() else None,
            image_dhash=row["image_dhash"] if "image_dhash" in row.keys() else None,
            cover_text=row["cover_text"] if "cover_text" in row.keys() else None,
            seller_text=row["seller_text"],
            raw_text=row["raw_text"],
            is_valid=bool(row["is_valid"]),
            invalid_reason=row["invalid_reason"],
        )
        for row in rows
    ]


def insert_sent_alert(
    db_path: str | Path,
    opportunity_id: int,
    alert_hash: str,
    channel: str,
    status: str,
    response_text: str | None = None,
) -> bool:
    init_db(db_path)
    try:
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                """
                INSERT INTO sent_alerts (opportunity_id, alert_hash, channel, status, response_text)
                VALUES (?, ?, ?, ?, ?)
                """,
                (opportunity_id, alert_hash, channel, status, response_text),
            )
        return True
    except sqlite3.IntegrityError:
        return False


ALLOWED_REVIEW_RESULTS = {
    "accepted_for_personal_collection",
    "rejected_version_mismatch",
    "rejected_xianyu_noise",
    "rejected_low_profit",
    "rejected_sold_out",
    "rejected_condition_bad",
    "rejected_liquidity_poor",
    "rejected_other",
}


def insert_review_decision(
    db_path: str | Path,
    opportunity_id: int,
    result: str,
    note: str | None = None,
) -> int:
    if result not in ALLOWED_REVIEW_RESULTS:
        raise ValueError(f"Unsupported review result: {result}")
    init_db(db_path)
    with sqlite3.connect(db_path) as conn:
        cur = conn.execute(
            """
            INSERT INTO review_decisions (opportunity_id, result, note)
            VALUES (?, ?, ?)
            """,
            (opportunity_id, result, note),
        )
        return int(cur.lastrowid)


def list_review_decisions(
    db_path: str | Path,
    opportunity_id: int | None = None,
    limit: int = 50,
) -> list[dict[str, object]]:
    init_db(db_path)
    sql = """
        SELECT id, opportunity_id, result, note, reviewed_at
        FROM review_decisions
    """
    params: list[object] = []
    if opportunity_id is not None:
        sql += " WHERE opportunity_id = ?"
        params.append(opportunity_id)
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(limit)
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(sql, tuple(params)).fetchall()
    return [dict(row) for row in rows]


def list_sent_alerts(
    db_path: str | Path,
    opportunity_id: int | None = None,
    limit: int = 50,
) -> list[dict[str, object]]:
    init_db(db_path)
    sql = """
        SELECT id, opportunity_id, alert_hash, channel, status, sent_at, response_text
        FROM sent_alerts
    """
    params: list[object] = []
    if opportunity_id is not None:
        sql += " WHERE opportunity_id = ?"
        params.append(opportunity_id)
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(limit)
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(sql, tuple(params)).fetchall()
    return [dict(row) for row in rows]


def schedule_candidate_recheck(
    db_path: str | Path,
    opportunity_id: int,
    scheduled_at: str | datetime,
    reason: str | None = None,
) -> dict[str, object]:
    init_db(db_path)
    scheduled_at_text = scheduled_at.isoformat() if isinstance(scheduled_at, datetime) else scheduled_at
    with sqlite3.connect(db_path) as conn:
        cur = conn.execute(
            """
            UPDATE candidate_rechecks
            SET reason = ?, scheduled_at = ?, updated_at = CURRENT_TIMESTAMP
            WHERE opportunity_id = ? AND status = 'pending'
            """,
            (reason, scheduled_at_text, opportunity_id),
        )
        if cur.rowcount == 0:
            conn.execute(
                """
                INSERT INTO candidate_rechecks (opportunity_id, status, reason, scheduled_at)
                VALUES (?, 'pending', ?, ?)
                """,
                (opportunity_id, reason, scheduled_at_text),
            )
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """
            SELECT id, opportunity_id, status, reason, scheduled_at, created_at, updated_at
            FROM candidate_rechecks
            WHERE opportunity_id = ? AND status = 'pending'
            """,
            (opportunity_id,),
        ).fetchone()
    return dict(row)


def list_candidate_rechecks(
    db_path: str | Path,
    status: str | None = "pending",
    limit: int = 50,
    opportunity_id: int | None = None,
) -> list[dict[str, object]]:
    init_db(db_path)
    sql = """
        SELECT id, opportunity_id, status, reason, scheduled_at, created_at, updated_at
        FROM candidate_rechecks
    """
    params: list[object] = []
    filters: list[str] = []
    if status is not None:
        filters.append("status = ?")
        params.append(status)
    if opportunity_id is not None:
        filters.append("opportunity_id = ?")
        params.append(opportunity_id)
    if filters:
        sql += " WHERE " + " AND ".join(filters)
    sql += " ORDER BY scheduled_at ASC, id ASC LIMIT ?"
    params.append(limit)
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(sql, tuple(params)).fetchall()
    return [dict(row) for row in rows]


ALLOWED_RECHECK_STATUSES = {"confirmed", "rejected", "human_required"}


def update_candidate_recheck_status(
    db_path: str | Path,
    recheck_id: int,
    status: str,
    reason: str | None = None,
) -> dict[str, object]:
    if status not in ALLOWED_RECHECK_STATUSES:
        raise ValueError(f"Unsupported recheck status: {status}")
    init_db(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            UPDATE candidate_rechecks
            SET status = ?, reason = COALESCE(?, reason), updated_at = CURRENT_TIMESTAMP
            WHERE id = ? AND status = 'pending'
            """,
            (status, reason, recheck_id),
        )
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """
            SELECT id, opportunity_id, status, reason, scheduled_at, created_at, updated_at
            FROM candidate_rechecks
            WHERE id = ?
            """,
            (recheck_id,),
        ).fetchone()
    if row is None:
        raise KeyError(f"Candidate recheck not found: {recheck_id}")
    return dict(row)
