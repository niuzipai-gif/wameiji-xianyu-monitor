from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from cd_monitor.core.discovery import DiscoveryCandidate, DiscoveryKeyword, DiscoveryPool
from cd_monitor.core.dual_market import ListingObservation, MarketSource, PriceComparison
from cd_monitor.core.identifiers import normalize_catalog_no_compact
from cd_monitor.core.models import MarketItem, Opportunity, WatchItem, XianyuPriceSample
from cd_monitor.core.product_images import is_usable_product_image
from cd_monitor.storage.migrations import SCHEMA_SQL

_TITLE_QUERY_EVIDENCE_VERSION_KEY = "discovery_title_query_evidence_version"
_TITLE_QUERY_EVIDENCE_VERSION = "strict-title-sample-v3"
_DISCOVERY_UNVERIFIED_QUEUE_EPOCH_KEY = "discovery_unverified_queue_epoch"
_DISCOVERY_UNVERIFIED_QUEUE_EPOCH = "detail-first-baseline-v1"
_DISCOVERY_OPPORTUNITY_FRESHNESS_MINUTES = 180
_DISCOVERY_SOURCE_DETAIL_FRESHNESS_MINUTES = 180
_DISCOVERY_XIANYU_LOGIN_STATE_KEY = "discovery_xianyu_login_state"



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


def _migrate_title_only_xianyu_rechecks(conn: sqlite3.Connection) -> None:
    """Retry legacy title-only candidates once after title-match hardening.

    Earlier collector runs used a stricter/incorrect title matcher and then
    persisted the lookup timestamp. Only detail-verified, still-active
    candidates without a catalog/JAN are reset, so exact-identifier evidence
    and ordinary scan cadence remain intact.
    """

    row = conn.execute(
        "SELECT value FROM user_settings WHERE key = ?",
        (_TITLE_QUERY_EVIDENCE_VERSION_KEY,),
    ).fetchone()
    if row is not None and str(row[0]) == _TITLE_QUERY_EVIDENCE_VERSION:
        return
    conn.execute(
        """
        UPDATE discovery_candidates
        SET last_xianyu_checked_at = NULL, updated_at = CURRENT_TIMESTAMP
        WHERE detail_verified = 1
          AND status = 'active'
          AND last_xianyu_checked_at IS NOT NULL
          AND COALESCE(TRIM(catalog_no), '') = ''
          AND COALESCE(TRIM(jan), '') = ''
        """
    )
    conn.execute(
        """
        INSERT INTO user_settings (key, value, updated_at)
        VALUES (?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = CURRENT_TIMESTAMP
        """,
        (_TITLE_QUERY_EVIDENCE_VERSION_KEY, _TITLE_QUERY_EVIDENCE_VERSION),
    )


def _migrate_duplicate_source_candidates(conn: sqlite3.Connection) -> None:
    """Retire legacy catalog/title rows duplicated by a source-listing key.

    Early discovery runs keyed some cards by catalog/JAN before the stable
    Wameiji listing id was available. A later detail read correctly creates a
    ``source:...`` row, but leaving both rows active spends the detail budget
    twice and can leave an old evaluation on the selection board.
    """

    duplicate_sources = conn.execute(
        """
        SELECT pool_id, TRIM(source_item_id) AS source_item_id
        FROM discovery_candidates
        WHERE COALESCE(TRIM(source_item_id), '') != ''
        GROUP BY pool_id, TRIM(source_item_id)
        HAVING COUNT(*) > 1
        """
    ).fetchall()
    for pool_id, source_item_id in duplicate_sources:
        rows = conn.execute(
            """
            SELECT id, identity_key, detail_verified, status, last_xianyu_checked_at
            FROM discovery_candidates
            WHERE pool_id = ? AND TRIM(source_item_id) = ?
            """,
            (pool_id, source_item_id),
        ).fetchall()
        # Preserve the row with the strongest purchase evidence. When both
        # have equal evidence, the source-listing identity is canonical.
        canonical = max(
            rows,
            key=lambda row: (
                int(bool(row[2])),
                int(str(row[1] or "").startswith("source:")),
                int(str(row[3] or "") == "active"),
                int(row[4] is not None),
                int(row[0]),
            ),
        )
        canonical_id = int(canonical[0])
        for row in rows:
            duplicate_id = int(row[0])
            if duplicate_id == canonical_id:
                continue
            conn.execute(
                """
                UPDATE opportunities
                SET status = 'expired'
                WHERE discovery_candidate_id = ?
                  AND COALESCE(status, 'active') = 'active'
                """,
                (duplicate_id,),
            )
            conn.execute(
                """
                UPDATE discovery_candidates
                SET status = 'ignored', updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (duplicate_id,),
            )


def _migrate_duplicate_source_url_candidates(conn: sqlite3.Connection) -> None:
    """Retire rows that describe the same Wameiji detail URL.

    A historical parser could derive a different identity from search-card
    text before a later read retained the source listing id.  The URL is the
    shared primary evidence in that case.  Keep both rows for audit, but make
    only the best-evidenced row eligible for future queues and the board.
    """

    duplicate_urls = conn.execute(
        """
        SELECT pool_id, LOWER(TRIM(source_url)) AS source_url
        FROM discovery_candidates
        WHERE COALESCE(TRIM(source_url), '') != ''
        GROUP BY pool_id, LOWER(TRIM(source_url))
        HAVING COUNT(*) > 1
        """
    ).fetchall()
    for pool_id, source_url in duplicate_urls:
        rows = conn.execute(
            """
            SELECT id, identity_key, detail_verified, status, last_xianyu_checked_at
            FROM discovery_candidates
            WHERE pool_id = ? AND LOWER(TRIM(source_url)) = ?
            """,
            (pool_id, source_url),
        ).fetchall()
        canonical = max(
            rows,
            key=lambda row: (
                int(bool(row[2])),
                int(str(row[1] or "").startswith("source:")),
                int(str(row[3] or "") == "active"),
                int(row[4] is not None),
                int(row[0]),
            ),
        )
        canonical_id = int(canonical[0])
        for row in rows:
            duplicate_id = int(row[0])
            if duplicate_id == canonical_id:
                continue
            conn.execute(
                """
                UPDATE opportunities
                SET status = 'expired'
                WHERE discovery_candidate_id = ?
                  AND COALESCE(status, 'active') = 'active'
                """,
                (duplicate_id,),
            )
            conn.execute(
                """
                UPDATE discovery_candidates
                SET status = 'ignored', pipeline_stage = 'rejected',
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (duplicate_id,),
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
    * ``xianyu_price_sample_id`` — explicit automatic-discovery sample for display.
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
    if "xianyu_price_sample_id" not in existing:
        additions.append(("xianyu_price_sample_id", "INTEGER"))
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
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_opportunities_xianyu_price_sample"
        " ON opportunities(xianyu_price_sample_id)"
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


def _migrate_market_item_detail_fee_columns(conn: sqlite3.Connection) -> None:
    """Keep verified Wameiji fee rows available for later audit and rechecks."""
    cols = {row[1] for row in conn.execute("PRAGMA table_info(market_items)").fetchall()}
    additions = (
        ("japan_domestic_shipping_jpy", "REAL"),
        ("proxy_fee_jpy", "REAL"),
        ("fees_hint", "TEXT"),
    )
    for name, declaration in additions:
        if name not in cols:
            conn.execute(f"ALTER TABLE market_items ADD COLUMN {name} {declaration}")


def _migrate_discovery_selection_board(conn: sqlite3.Connection) -> None:
    """Create candidate-pool tables while leaving legacy watches untouched."""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS discovery_pools (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          slug TEXT NOT NULL UNIQUE,
          name TEXT NOT NULL,
          media_type TEXT NOT NULL,
          enabled INTEGER NOT NULL DEFAULT 1,
          scan_interval_minutes INTEGER NOT NULL DEFAULT 30,
          keyword_budget INTEGER NOT NULL DEFAULT 2,
          page_budget INTEGER NOT NULL DEFAULT 1,
          candidate_budget INTEGER NOT NULL DEFAULT 2,
          search_card_budget INTEGER NOT NULL DEFAULT 60,
          detail_budget INTEGER NOT NULL DEFAULT 4,
          xianyu_query_budget INTEGER NOT NULL DEFAULT 3,
          queue_high_watermark INTEGER NOT NULL DEFAULT 40,
          min_profit_cny REAL NOT NULL DEFAULT 35,
          min_margin REAL NOT NULL DEFAULT 0.25,
          min_match_confidence REAL NOT NULL DEFAULT 0.75,
          min_valid_xianyu_samples INTEGER NOT NULL DEFAULT 2,
          cost_overrides_json TEXT NOT NULL DEFAULT '{}',
          last_scanned_at TIMESTAMP,
          next_run_at TIMESTAMP,
          capture_state TEXT NOT NULL DEFAULT 'active',
          pause_reason TEXT,
          created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
          updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS discovery_keywords (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          pool_id INTEGER NOT NULL,
          keyword TEXT NOT NULL,
          weight INTEGER NOT NULL DEFAULT 1,
          enabled INTEGER NOT NULL DEFAULT 1,
          last_scanned_at TIMESTAMP,
          created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
          updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
          UNIQUE(pool_id, keyword),
          FOREIGN KEY(pool_id) REFERENCES discovery_pools(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS discovery_runs (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          pool_id INTEGER NOT NULL,
          keyword_id INTEGER,
          keyword TEXT,
          source TEXT NOT NULL,
          status TEXT NOT NULL,
          discovered_count INTEGER NOT NULL DEFAULT 0,
          candidate_count INTEGER NOT NULL DEFAULT 0,
          detail_query_count INTEGER NOT NULL DEFAULT 0,
          detail_verified_count INTEGER NOT NULL DEFAULT 0,
          detail_rejected_count INTEGER NOT NULL DEFAULT 0,
          evaluated_count INTEGER NOT NULL DEFAULT 0,
          xianyu_query_count INTEGER NOT NULL DEFAULT 0,
          resale_sampled_count INTEGER NOT NULL DEFAULT 0,
          error_type TEXT,
          error_message TEXT,
          screenshot_path TEXT,
          raw_snapshot_path TEXT,
          started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
          finished_at TIMESTAMP,
          FOREIGN KEY(pool_id) REFERENCES discovery_pools(id) ON DELETE CASCADE,
          FOREIGN KEY(keyword_id) REFERENCES discovery_keywords(id) ON DELETE SET NULL
        );

        CREATE TABLE IF NOT EXISTS discovery_source_cooldowns (
          source TEXT PRIMARY KEY,
          cooldown_until TIMESTAMP NOT NULL,
          reason TEXT,
          updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS discovery_candidates (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          pool_id INTEGER NOT NULL,
          media_type TEXT NOT NULL,
          identity_key TEXT NOT NULL,
          catalog_no TEXT,
          jan TEXT,
          title TEXT NOT NULL,
          artist TEXT,
          edition TEXT,
          source_item_id TEXT,
          source_url TEXT,
          source_image_url TEXT,
          source_price REAL NOT NULL DEFAULT 0,
          source_currency TEXT NOT NULL DEFAULT 'JPY',
          availability TEXT NOT NULL DEFAULT 'unknown_but_visible',
          status TEXT NOT NULL DEFAULT 'active',
          observation_count INTEGER NOT NULL DEFAULT 1,
          missing_scan_count INTEGER NOT NULL DEFAULT 0,
          last_xianyu_checked_at TIMESTAMP,
          raw_text TEXT,
          detail_verified INTEGER NOT NULL DEFAULT 0,
          pipeline_stage TEXT NOT NULL DEFAULT 'search_discovered',
          product_key TEXT,
          detail_attempt_count INTEGER NOT NULL DEFAULT 0,
          last_detail_attempt_at TIMESTAMP,
          last_detail_error TEXT,
          detail_verified_at TIMESTAMP,
          first_seen_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
          last_seen_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
          updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
          UNIQUE(pool_id, identity_key),
          FOREIGN KEY(pool_id) REFERENCES discovery_pools(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS discovery_title_alias_evidence (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          candidate_id INTEGER NOT NULL,
          source_title TEXT NOT NULL,
          alias TEXT NOT NULL,
          query TEXT NOT NULL,
          resolver TEXT NOT NULL,
          source_url TEXT NOT NULL,
          entity_id TEXT,
          created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
          updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
          UNIQUE(candidate_id, alias, query),
          FOREIGN KEY(candidate_id) REFERENCES discovery_candidates(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS collector_commands (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          remote_command_id TEXT,
          command_type TEXT NOT NULL,
          payload_json TEXT NOT NULL DEFAULT '{}',
          status TEXT NOT NULL DEFAULT 'pending',
          result_json TEXT,
          dedupe_key TEXT,
          created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
          accepted_at TIMESTAMP,
          completed_at TIMESTAMP,
          updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE UNIQUE INDEX IF NOT EXISTS idx_collector_commands_dedupe_pending
          ON collector_commands(dedupe_key)
          WHERE dedupe_key IS NOT NULL AND status IN ('pending', 'accepted', 'running');
        CREATE INDEX IF NOT EXISTS idx_discovery_candidates_pool_status
          ON discovery_candidates(pool_id, status, last_seen_at DESC);
        CREATE INDEX IF NOT EXISTS idx_discovery_title_alias_candidate
          ON discovery_title_alias_evidence(candidate_id, updated_at DESC);
        CREATE INDEX IF NOT EXISTS idx_discovery_runs_pool_time
          ON discovery_runs(pool_id, started_at DESC);
        """
    )
    collector_columns = {
        row[1] for row in conn.execute("PRAGMA table_info(collector_commands)").fetchall()
    }
    if "remote_command_id" not in collector_columns:
        conn.execute("ALTER TABLE collector_commands ADD COLUMN remote_command_id TEXT")
    candidate_columns = {
        row[1] for row in conn.execute("PRAGMA table_info(discovery_candidates)").fetchall()
    }
    if "detail_verified" not in candidate_columns:
        conn.execute(
            "ALTER TABLE discovery_candidates ADD COLUMN detail_verified INTEGER NOT NULL DEFAULT 0"
        )
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_collector_commands_remote_id "
        "ON collector_commands(remote_command_id)"
    )
    opportunity_columns = {
        row[1] for row in conn.execute("PRAGMA table_info(opportunities)").fetchall()
    }
    additions: list[tuple[str, str]] = []
    if "discovery_candidate_id" not in opportunity_columns:
        additions.append(("discovery_candidate_id", "INTEGER"))
    if "media_type" not in opportunity_columns:
        additions.append(("media_type", "TEXT"))
    if "identity_key" not in opportunity_columns:
        additions.append(("identity_key", "TEXT"))
    if "last_seen_at" not in opportunity_columns:
        additions.append(("last_seen_at", "TIMESTAMP"))
    for column, declaration in additions:
        conn.execute(f"ALTER TABLE opportunities ADD COLUMN {column} {declaration}")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_opportunities_discovery_candidate "
        "ON opportunities(discovery_candidate_id)"
    )
    conn.execute(
        """
        INSERT OR IGNORE INTO discovery_pools (
          slug, name, media_type, enabled, scan_interval_minutes,
          keyword_budget, page_budget, candidate_budget
        ) VALUES
          ('cd', 'CD 选品池', 'cd', 1, 30, 2, 1, 2),
          ('physical-game', '实体游戏选品池', 'physical_game', 1, 30, 2, 1, 2)
        """
    )
    conn.executescript(
        """
        INSERT OR IGNORE INTO discovery_keywords (pool_id, keyword, weight)
        SELECT id, '初回限定盤', 3 FROM discovery_pools WHERE slug = 'cd';
        INSERT OR IGNORE INTO discovery_keywords (pool_id, keyword, weight)
        SELECT id, '帯付き', 2 FROM discovery_pools WHERE slug = 'cd';
        INSERT OR IGNORE INTO discovery_keywords (pool_id, keyword, weight)
        SELECT id, '廃盤', 2 FROM discovery_pools WHERE slug = 'cd';
        INSERT OR IGNORE INTO discovery_keywords (pool_id, keyword, weight)
        SELECT id, 'Switch 限定版', 3 FROM discovery_pools WHERE slug = 'physical-game';
        INSERT OR IGNORE INTO discovery_keywords (pool_id, keyword, weight)
        SELECT id, 'PS Vita 限定版', 2 FROM discovery_pools WHERE slug = 'physical-game';
        INSERT OR IGNORE INTO discovery_keywords (pool_id, keyword, weight)
        SELECT id, '3DS 限定版', 2 FROM discovery_pools WHERE slug = 'physical-game';
        """
    )
    # ``next_run_at`` is derived state. Rebuild it during every initialization
    # so databases created before pool-level throttling do not show stale
    # per-keyword deadlines in the dashboard.
    conn.execute(
        """
        UPDATE discovery_pools
        SET next_run_at = CASE
          WHEN last_scanned_at IS NULL THEN CURRENT_TIMESTAMP
          ELSE datetime(last_scanned_at, '+' || scan_interval_minutes || ' minutes')
        END
        WHERE (last_scanned_at IS NULL AND next_run_at IS NULL)
           OR (
             last_scanned_at IS NOT NULL
             AND (
               next_run_at IS NULL
               OR datetime(next_run_at) != datetime(
                 last_scanned_at, '+' || scan_interval_minutes || ' minutes'
               )
             )
           )
        """
    )
    # Older detail parsing read the entire document. Every Wameiji page footer
    # includes "All Rights Reserved", which was incorrectly stored as a
    # product-level reservation. Re-open only those affected records so they
    # receive a fresh, component-scoped detail check before any Xianyu lookup.
    conn.execute(
        """
        UPDATE discovery_candidates
        SET availability = 'unknown_but_visible',
            status = 'active',
            detail_verified = 0,
            last_xianyu_checked_at = NULL,
            updated_at = CURRENT_TIMESTAMP
        WHERE detail_verified = 1
          AND availability = 'reserved'
          AND raw_text LIKE '%All Rights Reserved%'
        """
    )


def _migrate_detail_first_discovery(conn: sqlite3.Connection) -> None:
    """Upgrade selection storage for persistent detail-first collection.

    Search cards remain historical observations, but only source-detail rows
    can enter the resale queue.  All additions are nullable/defaulted so a
    pre-existing local collector database remains readable throughout the
    migration.
    """

    pool_columns = {
        str(row[1]) for row in conn.execute("PRAGMA table_info(discovery_pools)").fetchall()
    }
    for name, declaration in (
        ("search_card_budget", "INTEGER NOT NULL DEFAULT 60"),
        ("detail_budget", "INTEGER NOT NULL DEFAULT 4"),
        ("xianyu_query_budget", "INTEGER NOT NULL DEFAULT 3"),
        ("queue_high_watermark", "INTEGER NOT NULL DEFAULT 40"),
        ("capture_state", "TEXT NOT NULL DEFAULT 'active'"),
        ("pause_reason", "TEXT"),
    ):
        if name not in pool_columns:
            conn.execute(f"ALTER TABLE discovery_pools ADD COLUMN {name} {declaration}")

    candidate_columns = {
        str(row[1])
        for row in conn.execute("PRAGMA table_info(discovery_candidates)").fetchall()
    }
    for name, declaration in (
        ("source_image_url", "TEXT"),
        ("pipeline_stage", "TEXT NOT NULL DEFAULT 'search_discovered'"),
        ("product_key", "TEXT"),
        ("detail_attempt_count", "INTEGER NOT NULL DEFAULT 0"),
        ("last_detail_attempt_at", "TIMESTAMP"),
        ("last_detail_error", "TEXT"),
        ("detail_verified_at", "TIMESTAMP"),
    ):
        if name not in candidate_columns:
            conn.execute(f"ALTER TABLE discovery_candidates ADD COLUMN {name} {declaration}")

    run_columns = {
        str(row[1]) for row in conn.execute("PRAGMA table_info(discovery_runs)").fetchall()
    }
    for name, declaration in (
        ("detail_query_count", "INTEGER NOT NULL DEFAULT 0"),
        ("detail_verified_count", "INTEGER NOT NULL DEFAULT 0"),
        ("detail_rejected_count", "INTEGER NOT NULL DEFAULT 0"),
        ("xianyu_query_count", "INTEGER NOT NULL DEFAULT 0"),
        ("resale_sampled_count", "INTEGER NOT NULL DEFAULT 0"),
    ):
        if name not in run_columns:
            conn.execute(f"ALTER TABLE discovery_runs ADD COLUMN {name} {declaration}")

    conn.execute(
        """
        UPDATE discovery_candidates
        SET pipeline_stage = CASE
          WHEN status = 'ignored' THEN 'rejected'
          WHEN detail_verified = 1 AND last_xianyu_checked_at IS NOT NULL THEN 'evaluated'
          WHEN detail_verified = 1 THEN 'resale_queued'
          WHEN status = 'active' THEN 'detail_queued'
          ELSE 'blocked'
        END,
        detail_verified_at = CASE
          WHEN detail_verified = 1 THEN COALESCE(detail_verified_at, last_seen_at)
          ELSE detail_verified_at
        END
        WHERE pipeline_stage IS NULL
           OR TRIM(pipeline_stage) = ''
           OR (pipeline_stage = 'search_discovered' AND detail_verified = 1)
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_discovery_candidates_detail_queue "
        "ON discovery_candidates(pool_id, status, detail_verified, first_seen_at, id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_discovery_candidates_product_queue "
        "ON discovery_candidates(pool_id, product_key, last_xianyu_checked_at, id)"
    )


def _migrate_unverified_discovery_queue_epoch(conn: sqlite3.Connection) -> None:
    """Quarantine search-only rows that predate the detail-first queue.

    Previous collector runs saved hundreds of broad search cards without
    opening their product pages. Treating those stale observations as a new
    detail queue makes the collector spend days on old, unproven URLs before
    it can inspect a fresh source result. Keep them for audit, but reserve the
    post-migration queue exclusively for cards discovered by the new flow.
    """

    row = conn.execute(
        "SELECT value FROM user_settings WHERE key = ?",
        (_DISCOVERY_UNVERIFIED_QUEUE_EPOCH_KEY,),
    ).fetchone()
    if row is not None and str(row[0]) == _DISCOVERY_UNVERIFIED_QUEUE_EPOCH:
        return

    eligible = """
        status = 'active'
        AND COALESCE(detail_verified, 0) = 0
        AND COALESCE(TRIM(pipeline_stage), 'search_discovered') IN (
          'search_discovered', 'detail_queued'
        )
    """
    conn.execute(
        f"""
        UPDATE opportunities
        SET status = 'expired'
        WHERE discovery_candidate_id IN (
          SELECT id FROM discovery_candidates WHERE {eligible}
        )
          AND COALESCE(status, 'active') = 'active'
        """
    )
    conn.execute(
        f"""
        UPDATE discovery_candidates
        SET status = 'ignored',
            pipeline_stage = 'quarantined',
            last_detail_error = 'pre_detail_queue_epoch',
            updated_at = CURRENT_TIMESTAMP
        WHERE {eligible}
        """
    )
    conn.execute(
        """
        INSERT INTO user_settings (key, value, updated_at)
        VALUES (?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = CURRENT_TIMESTAMP
        """,
        (_DISCOVERY_UNVERIFIED_QUEUE_EPOCH_KEY, _DISCOVERY_UNVERIFIED_QUEUE_EPOCH),
    )


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
            _migrate_market_item_detail_fee_columns(conn)
            _migrate_discovery_selection_board(conn)
            _migrate_detail_first_discovery(conn)
            _migrate_duplicate_source_candidates(conn)
            _migrate_duplicate_source_url_candidates(conn)
            _migrate_unverified_discovery_queue_epoch(conn)
            _migrate_title_only_xianyu_rechecks(conn)
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
                  price, currency, price_cny_display, japan_domestic_shipping_jpy, proxy_fee_jpy, fees_hint,
                  url, image_url, availability, condition_text, raw_text)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    item.japan_domestic_shipping_jpy,
                    item.proxy_fee_jpy,
                    item.fees_hint,
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


def insert_listing_observation(
    db_path: str | Path, observation: ListingObservation
) -> int:
    """Persist one immutable capture and return its stable database id.

    Replaying the same evidence manifest is idempotent, while a later capture
    of the same source listing remains a new historical row because its
    ``captured_at`` differs.
    """

    init_db(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO listing_observations (
              source, source_listing_id, canonical_product_key, title, price, currency,
              url, image_url, availability, condition_group, completeness, evidence_level,
              raw_snapshot_path, screenshot_path, source_detail_fee, captured_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(source, source_listing_id, captured_at) DO NOTHING
            """,
            (
                observation.source,
                observation.source_listing_id,
                observation.canonical_product_key,
                observation.title,
                observation.price,
                observation.currency,
                observation.url,
                observation.image_url,
                observation.availability,
                observation.condition_group,
                observation.completeness,
                observation.evidence_level,
                observation.raw_snapshot_path,
                observation.screenshot_path,
                observation.source_detail_fee,
                observation.captured_at,
            ),
        )
        row = conn.execute(
            """
            SELECT id FROM listing_observations
            WHERE source = ? AND source_listing_id = ? AND captured_at = ?
            """,
            (
                observation.source,
                observation.source_listing_id,
                observation.captured_at,
            ),
        ).fetchone()
    if row is None:  # pragma: no cover - SQLite invariant guard
        raise RuntimeError("listing observation was not persisted")
    return int(row[0])


def get_listing_observation(
    db_path: str | Path, observation_id: int
) -> ListingObservation | None:
    init_db(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM listing_observations WHERE id = ?", (observation_id,)
        ).fetchone()
    return _listing_observation_from_row(row) if row is not None else None


def list_current_observations(
    db_path: str | Path,
    *,
    canonical_product_key: str | None = None,
    source: MarketSource | None = None,
    captured_since: str | None = None,
) -> list[ListingObservation]:
    """Return the newest capture per source-local listing without deleting history."""

    init_db(db_path)
    conditions: list[str] = []
    parameters: list[object] = []
    if canonical_product_key is not None:
        conditions.append("canonical_product_key = ?")
        parameters.append(canonical_product_key)
    if source is not None:
        conditions.append("source = ?")
        parameters.append(source)
    if captured_since is not None:
        conditions.append("datetime(captured_at) >= datetime(?)")
        parameters.append(captured_since)
    where_sql = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    sql = f"""
        WITH current_per_listing AS (
          SELECT *, ROW_NUMBER() OVER (
            PARTITION BY source, source_listing_id
            ORDER BY captured_at DESC, id DESC
          ) AS current_rank
          FROM listing_observations
          {where_sql}
        )
        SELECT * FROM current_per_listing
        WHERE current_rank = 1
        ORDER BY captured_at DESC, id DESC
    """
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(sql, tuple(parameters)).fetchall()
    return [_listing_observation_from_row(row) for row in rows]


def _listing_observation_from_row(row: sqlite3.Row) -> ListingObservation:
    return ListingObservation(
        source=row["source"],
        source_listing_id=row["source_listing_id"],
        canonical_product_key=row["canonical_product_key"],
        title=row["title"],
        price=float(row["price"]),
        currency=row["currency"],
        url=row["url"],
        image_url=row["image_url"],
        availability=row["availability"],
        condition_group=row["condition_group"],
        completeness=row["completeness"],
        evidence_level=row["evidence_level"],
        captured_at=row["captured_at"],
        raw_snapshot_path=row["raw_snapshot_path"],
        screenshot_path=row["screenshot_path"],
        source_detail_fee=row["source_detail_fee"],
        id=int(row["id"]),
    )


def insert_price_comparison(db_path: str | Path, comparison: PriceComparison) -> int:
    """Append a comparison snapshot; never overwrite an older calculation."""

    init_db(db_path)
    with sqlite3.connect(db_path) as conn:
        cur = conn.execute(
            """
            INSERT INTO price_comparisons (
              canonical_product_key, wameiji_observation_id, xianyu_observation_id,
              cost_config_json, landed_cost_cny, sale_price_cny, expected_profit_cny,
              net_margin, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                comparison.canonical_product_key,
                comparison.wameiji_observation_id,
                comparison.xianyu_observation_id,
                comparison.cost_config_json,
                comparison.landed_cost_cny,
                comparison.sale_price_cny,
                comparison.expected_profit_cny,
                comparison.net_margin,
                comparison.status,
            ),
        )
    return int(cur.lastrowid)


def list_price_comparisons(
    db_path: str | Path,
    *,
    canonical_product_key: str | None = None,
    limit: int = 100,
) -> list[PriceComparison]:
    """List persisted comparison snapshots newest first for board/API readers."""

    init_db(db_path)
    where_sql = ""
    parameters: tuple[object, ...] = (max(1, int(limit)),)
    if canonical_product_key is not None:
        where_sql = "WHERE canonical_product_key = ?"
        parameters = (canonical_product_key, max(1, int(limit)))
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            f"""
            SELECT * FROM price_comparisons
            {where_sql}
            ORDER BY id DESC
            LIMIT ?
            """,
            parameters,
        ).fetchall()
    return [_price_comparison_from_row(row) for row in rows]


def _price_comparison_from_row(row: sqlite3.Row) -> PriceComparison:
    return PriceComparison(
        id=int(row["id"]),
        canonical_product_key=row["canonical_product_key"],
        wameiji_observation_id=int(row["wameiji_observation_id"]),
        xianyu_observation_id=int(row["xianyu_observation_id"]),
        cost_config_json=row["cost_config_json"],
        landed_cost_cny=(
            float(row["landed_cost_cny"]) if row["landed_cost_cny"] is not None else None
        ),
        sale_price_cny=float(row["sale_price_cny"]),
        expected_profit_cny=(
            float(row["expected_profit_cny"])
            if row["expected_profit_cny"] is not None
            else None
        ),
        net_margin=float(row["net_margin"]) if row["net_margin"] is not None else None,
        status=row["status"],
        created_at=row["created_at"],
    )


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


def list_discovery_pools(db_path: str | Path) -> list[DiscoveryPool]:
    init_db(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT id, slug, name, media_type, enabled, scan_interval_minutes,
              keyword_budget, page_budget, candidate_budget, search_card_budget,
              detail_budget, xianyu_query_budget, queue_high_watermark, min_profit_cny,
              min_margin, min_match_confidence, min_valid_xianyu_samples,
              cost_overrides_json, last_scanned_at, next_run_at, capture_state,
              pause_reason
            FROM discovery_pools
            ORDER BY id ASC
            """
        ).fetchall()
    return [
        DiscoveryPool(
            id=int(row["id"]),
            slug=row["slug"],
            name=row["name"],
            media_type=row["media_type"],
            enabled=bool(row["enabled"]),
            scan_interval_minutes=int(row["scan_interval_minutes"]),
            keyword_budget=int(row["keyword_budget"]),
            page_budget=int(row["page_budget"]),
            candidate_budget=int(row["candidate_budget"]),
            search_card_budget=int(row["search_card_budget"]),
            detail_budget=int(row["detail_budget"]),
            xianyu_query_budget=int(row["xianyu_query_budget"]),
            queue_high_watermark=int(row["queue_high_watermark"]),
            min_profit_cny=float(row["min_profit_cny"]),
            min_margin=float(row["min_margin"]),
            min_match_confidence=float(row["min_match_confidence"]),
            min_valid_xianyu_samples=int(row["min_valid_xianyu_samples"]),
            cost_overrides_json=row["cost_overrides_json"] or "{}",
            last_scanned_at=row["last_scanned_at"],
            next_run_at=row["next_run_at"],
            capture_state=row["capture_state"] or "active",
            pause_reason=row["pause_reason"],
        )
        for row in rows
    ]


def get_discovery_pool(db_path: str | Path, pool_id: int) -> DiscoveryPool:
    for pool in list_discovery_pools(db_path):
        if pool.id == pool_id:
            return pool
    raise KeyError(f"Discovery pool not found: {pool_id}")


def list_discovery_keywords(
    db_path: str | Path, pool_id: int | None = None
) -> list[DiscoveryKeyword]:
    init_db(db_path)
    sql = """
        SELECT id, pool_id, keyword, weight, enabled, last_scanned_at
        FROM discovery_keywords
    """
    params: tuple[object, ...] = ()
    if pool_id is not None:
        sql += " WHERE pool_id = ?"
        params = (pool_id,)
    sql += " ORDER BY pool_id ASC, weight DESC, id ASC"
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(sql, params).fetchall()
    return [
        DiscoveryKeyword(
            id=int(row["id"]),
            pool_id=int(row["pool_id"]),
            keyword=row["keyword"],
            weight=int(row["weight"]),
            enabled=bool(row["enabled"]),
            last_scanned_at=row["last_scanned_at"],
        )
        for row in rows
    ]


def upsert_discovery_keyword(
    db_path: str | Path,
    *,
    pool_id: int,
    keyword: str,
    weight: int = 1,
    enabled: bool = True,
) -> DiscoveryKeyword:
    normalized = " ".join(str(keyword or "").strip().split())
    if not normalized:
        raise ValueError("keyword_required")
    init_db(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO discovery_keywords (pool_id, keyword, weight, enabled)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(pool_id, keyword) DO UPDATE SET
              weight = excluded.weight, enabled = excluded.enabled,
              updated_at = CURRENT_TIMESTAMP
            """,
            (pool_id, normalized, max(1, int(weight)), int(enabled)),
        )
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """
            SELECT id, pool_id, keyword, weight, enabled, last_scanned_at
            FROM discovery_keywords WHERE pool_id = ? AND keyword = ?
            """,
            (pool_id, normalized),
        ).fetchone()
    if row is None:
        raise RuntimeError("Discovery keyword was not persisted")
    return DiscoveryKeyword(
        id=int(row["id"]),
        pool_id=int(row["pool_id"]),
        keyword=row["keyword"],
        weight=int(row["weight"]),
        enabled=bool(row["enabled"]),
        last_scanned_at=row["last_scanned_at"],
    )


def replace_discovery_keywords(
    db_path: str | Path,
    pool_id: int,
    keywords: list[dict[str, object]],
) -> list[DiscoveryKeyword]:
    """Replace a pool's enabled keyword set without deleting its history."""
    normalized: list[tuple[str, int, bool]] = []
    seen: set[str] = set()
    for item in keywords:
        keyword = " ".join(str(item.get("keyword") or "").strip().split())
        if not keyword or keyword in seen:
            continue
        seen.add(keyword)
        try:
            weight = max(1, int(item.get("weight") or 1))
        except (TypeError, ValueError):
            weight = 1
        normalized.append((keyword, weight, bool(item.get("enabled", True))))

    init_db(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            UPDATE discovery_keywords
            SET enabled = 0, updated_at = CURRENT_TIMESTAMP
            WHERE pool_id = ?
            """,
            (pool_id,),
        )
        for keyword, weight, enabled in normalized:
            conn.execute(
                """
                INSERT INTO discovery_keywords (pool_id, keyword, weight, enabled)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(pool_id, keyword) DO UPDATE SET
                  weight = excluded.weight,
                  enabled = excluded.enabled,
                  updated_at = CURRENT_TIMESTAMP
                """,
                (pool_id, keyword, weight, int(enabled)),
            )
        _refresh_discovery_pool_next_run(conn, pool_id)
    return list_discovery_keywords(db_path, pool_id)


def list_due_discovery_keywords(db_path: str | Path, pool_id: int) -> list[DiscoveryKeyword]:
    pool = get_discovery_pool(db_path, pool_id)
    if not pool.enabled:
        return []
    init_db(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT k.id, k.pool_id, k.keyword, k.weight, k.enabled, k.last_scanned_at
            FROM discovery_keywords AS k
            JOIN discovery_pools AS p ON p.id = k.pool_id
            WHERE k.pool_id = ? AND k.enabled = 1 AND p.enabled = 1
              AND (
                p.last_scanned_at IS NULL
                OR datetime(p.last_scanned_at, '+' || p.scan_interval_minutes || ' minutes')
                   <= CURRENT_TIMESTAMP
              )
              AND (
                k.last_scanned_at IS NULL
                OR datetime(k.last_scanned_at, '+' || ? || ' minutes') <= CURRENT_TIMESTAMP
              )
            ORDER BY k.weight DESC, k.last_scanned_at ASC, k.id ASC
            LIMIT ?
            """,
            (pool_id, pool.scan_interval_minutes, max(1, pool.keyword_budget)),
        ).fetchall()
    return [
        DiscoveryKeyword(
            id=int(row["id"]),
            pool_id=int(row["pool_id"]),
            keyword=row["keyword"],
            weight=int(row["weight"]),
            enabled=bool(row["enabled"]),
            last_scanned_at=row["last_scanned_at"],
        )
        for row in rows
    ]


def mark_discovery_keyword_scanned(db_path: str | Path, keyword_id: int) -> None:
    init_db(db_path)
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT pool_id FROM discovery_keywords WHERE id = ?", (keyword_id,)
        ).fetchone()
        conn.execute(
            """
            UPDATE discovery_keywords
            SET last_scanned_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (keyword_id,),
        )
        if row is not None:
            _refresh_discovery_pool_next_run(conn, int(row[0]))


def update_discovery_pool(
    db_path: str | Path, pool_id: int, updates: dict[str, object]
) -> DiscoveryPool:
    allowed = {
        "enabled",
        "scan_interval_minutes",
        "keyword_budget",
        "page_budget",
        "candidate_budget",
        "search_card_budget",
        "detail_budget",
        "xianyu_query_budget",
        "queue_high_watermark",
        "capture_state",
        "pause_reason",
        "min_profit_cny",
        "min_margin",
        "min_match_confidence",
        "min_valid_xianyu_samples",
        "cost_overrides_json",
    }
    values = {key: value for key, value in updates.items() if key in allowed}
    if "candidate_budget" in values:
        # Backward compatibility for saved UI settings from the one-budget
        # collector. New callers set the independent budgets directly.
        values.setdefault("detail_budget", values["candidate_budget"])
        values.setdefault("xianyu_query_budget", values["candidate_budget"])
    if values.get("capture_state") == "active" and "pause_reason" not in values:
        values["pause_reason"] = None
    if values:
        init_db(db_path)
        assignments = ", ".join(f"{key} = ?" for key in values)
        params = list(values.values()) + [pool_id]
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                f"UPDATE discovery_pools SET {assignments}, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                tuple(params),
            )
            _refresh_discovery_pool_next_run(conn, pool_id)
    return get_discovery_pool(db_path, pool_id)


def set_discovery_pool_capture_state(
    db_path: str | Path,
    pool_id: int,
    *,
    capture_state: str,
    pause_reason: str | None = None,
) -> DiscoveryPool:
    """Change automatic capture state without disabling the user's pool."""

    normalized = str(capture_state or "").strip().lower()
    if normalized not in {"active", "paused_quality"}:
        raise ValueError("invalid_capture_state")
    return update_discovery_pool(
        db_path,
        pool_id,
        {
            "capture_state": normalized,
            "pause_reason": pause_reason if normalized == "paused_quality" else None,
        },
    )


def get_discovery_candidate_by_identity(
    db_path: str | Path, pool_id: int, identity_key: str
) -> DiscoveryCandidate | None:
    init_db(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        row = _select_discovery_candidate_by_identity(conn, pool_id, identity_key)
    if row is None:
        return None
    return _discovery_candidate_from_row(row)


def get_discovery_candidate(db_path: str | Path, candidate_id: int) -> DiscoveryCandidate:
    init_db(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """
            SELECT id, pool_id, media_type, identity_key, catalog_no, jan, title,
              artist, edition, source_item_id, source_url, source_image_url, source_price,
              source_currency, availability, status, observation_count,
              missing_scan_count, last_xianyu_checked_at, raw_text, detail_verified,
              pipeline_stage, product_key, detail_attempt_count,
              last_detail_attempt_at, last_detail_error, detail_verified_at
            FROM discovery_candidates WHERE id = ?
            """,
            (candidate_id,),
        ).fetchone()
    if row is None:
        raise KeyError(f"Discovery candidate not found: {candidate_id}")
    return _discovery_candidate_from_row(row)


def list_discovery_detail_queue(
    db_path: str | Path, pool_id: int, *, limit: int
) -> list[DiscoveryCandidate]:
    """Return outstanding source links independently of the latest search page.

    The service applies product-specific ranking before opening browser pages;
    this storage query deliberately preserves oldest-first queue fairness so a
    later search result cannot starve an earlier detail candidate.
    """

    init_db(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT id, pool_id, media_type, identity_key, catalog_no, jan, title,
              artist, edition, source_item_id, source_url, source_image_url, source_price,
              source_currency, availability, status, observation_count,
              missing_scan_count, last_xianyu_checked_at, raw_text, detail_verified,
              pipeline_stage, product_key, detail_attempt_count,
              last_detail_attempt_at, last_detail_error, detail_verified_at
            FROM discovery_candidates
            WHERE pool_id = ?
              AND status = 'active'
              AND (
                COALESCE(TRIM(source_url), '') != ''
                OR COALESCE(TRIM(source_item_id), '') != ''
              )
              AND (
                (
                  detail_verified = 0
                  AND COALESCE(pipeline_stage, 'search_discovered') IN (
                    'search_discovered', 'detail_queued'
                  )
                )
                OR (
                  detail_verified = 1
                  AND detail_verified_at IS NOT NULL
                  AND datetime(detail_verified_at) <= datetime(CURRENT_TIMESTAMP, ?)
                )
              )
            ORDER BY datetime(first_seen_at) ASC, id ASC
            LIMIT ?
            """,
            (
                pool_id,
                f"-{_DISCOVERY_SOURCE_DETAIL_FRESHNESS_MINUTES} minutes",
                max(0, int(limit)),
            ),
        ).fetchall()
    return [_discovery_candidate_from_row(row) for row in rows]


def list_discovery_resale_queue(
    db_path: str | Path,
    pool_id: int,
    *,
    limit: int,
    refresh_after_minutes: int = 180,
) -> list[DiscoveryCandidate]:
    """Return unobserved or stale verified details for a resale observation."""

    init_db(db_path)
    refresh_window = max(1, int(refresh_after_minutes))
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT id, pool_id, media_type, identity_key, catalog_no, jan, title,
              artist, edition, source_item_id, source_url, source_image_url, source_price,
              source_currency, availability, status, observation_count,
              missing_scan_count, last_xianyu_checked_at, raw_text, detail_verified,
              pipeline_stage, product_key, detail_attempt_count,
              last_detail_attempt_at, last_detail_error, detail_verified_at
            FROM discovery_candidates
            WHERE pool_id = ?
              AND status = 'active'
              AND detail_verified = 1
              AND detail_verified_at IS NOT NULL
              AND datetime(detail_verified_at) >= datetime(CURRENT_TIMESTAMP, ?)
              AND (
                last_xianyu_checked_at IS NULL
                OR datetime(last_xianyu_checked_at) <= datetime(CURRENT_TIMESTAMP, ?)
              )
            ORDER BY
              CASE WHEN last_xianyu_checked_at IS NULL THEN 0 ELSE 1 END,
              CASE WHEN COALESCE(TRIM(catalog_no), '') != ''
                      OR COALESCE(TRIM(jan), '') != '' THEN 0 ELSE 1 END,
              datetime(COALESCE(last_xianyu_checked_at, detail_verified_at)) ASC,
              id ASC
            LIMIT ?
            """,
            (
                pool_id,
                f"-{_DISCOVERY_SOURCE_DETAIL_FRESHNESS_MINUTES} minutes",
                f"-{refresh_window} minutes",
                max(0, int(limit)),
            ),
        ).fetchall()
    return [_discovery_candidate_from_row(row) for row in rows]


def _discovery_candidate_from_row(row: sqlite3.Row) -> DiscoveryCandidate:
    return DiscoveryCandidate(
        id=int(row["id"]),
        pool_id=int(row["pool_id"]),
        media_type=row["media_type"],
        identity_key=row["identity_key"],
        catalog_no=row["catalog_no"],
        jan=row["jan"],
        title=row["title"],
        artist=row["artist"],
        edition=row["edition"],
        source_item_id=row["source_item_id"],
        source_url=row["source_url"],
        source_image_url=row["source_image_url"],
        source_price=float(row["source_price"]),
        source_currency=row["source_currency"],
        availability=row["availability"],
        status=row["status"],
        observation_count=int(row["observation_count"]),
        missing_scan_count=int(row["missing_scan_count"]),
        last_xianyu_checked_at=row["last_xianyu_checked_at"],
        raw_text=row["raw_text"],
        detail_verified=bool(row["detail_verified"]),
        pipeline_stage=row["pipeline_stage"] or "search_discovered",
        product_key=row["product_key"],
        detail_attempt_count=int(row["detail_attempt_count"]),
        last_detail_attempt_at=row["last_detail_attempt_at"],
        last_detail_error=row["last_detail_error"],
        detail_verified_at=row["detail_verified_at"],
    )


def _select_discovery_candidate_by_identity(
    conn: sqlite3.Connection, pool_id: int, identity_key: str
) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT id, pool_id, media_type, identity_key, catalog_no, jan, title,
          artist, edition, source_item_id, source_url, source_image_url, source_price,
          source_currency, availability, status, observation_count,
          missing_scan_count, last_xianyu_checked_at, raw_text, detail_verified,
          pipeline_stage, product_key, detail_attempt_count,
          last_detail_attempt_at, last_detail_error, detail_verified_at
        FROM discovery_candidates
        WHERE pool_id = ? AND identity_key = ?
        """,
        (pool_id, identity_key),
    ).fetchone()


def record_discovery_run(
    db_path: str | Path,
    *,
    pool_id: int,
    source: str,
    status: str,
    keyword: str | None = None,
    keyword_id: int | None = None,
    discovered_count: int = 0,
    candidate_count: int = 0,
    detail_query_count: int = 0,
    detail_verified_count: int = 0,
    detail_rejected_count: int = 0,
    evaluated_count: int = 0,
    xianyu_query_count: int = 0,
    resale_sampled_count: int = 0,
    error_type: str | None = None,
    error_message: str | None = None,
    screenshot_path: str | None = None,
    raw_snapshot_path: str | None = None,
) -> int:
    init_db(db_path)
    with sqlite3.connect(db_path) as conn:
        cursor = conn.execute(
            """
            INSERT INTO discovery_runs (
              pool_id, keyword_id, keyword, source, status, discovered_count,
              candidate_count, detail_query_count, detail_verified_count,
              detail_rejected_count, evaluated_count, xianyu_query_count,
              resale_sampled_count, error_type, error_message, screenshot_path,
              raw_snapshot_path, finished_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            """,
            (
                pool_id,
                keyword_id,
                keyword,
                source,
                status,
                discovered_count,
                candidate_count,
                detail_query_count,
                detail_verified_count,
                detail_rejected_count,
                evaluated_count,
                xianyu_query_count,
                resale_sampled_count,
                error_type,
                error_message,
                screenshot_path,
                raw_snapshot_path,
            ),
        )
    return int(cursor.lastrowid)


def mark_discovery_candidate_xianyu_checked(db_path: str | Path, candidate_id: int) -> None:
    init_db(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            UPDATE discovery_candidates
            SET last_xianyu_checked_at = CURRENT_TIMESTAMP, pipeline_stage = 'evaluated',
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (candidate_id,),
        )


def record_discovery_candidate_detail_attempt(
    db_path: str | Path,
    candidate_id: int,
    *,
    pipeline_stage: str,
    detail_verified: bool = False,
    error_message: str | None = None,
    product_key: str | None = None,
    increment_attempt: bool = True,
) -> None:
    """Persist one source-detail outcome for queue recovery and diagnostics."""

    if pipeline_stage not in {
        "detail_queued",
        "resale_queued",
        "blocked",
        "rejected",
    }:
        raise ValueError("invalid_discovery_pipeline_stage")
    init_db(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            UPDATE discovery_candidates
            SET detail_attempt_count = detail_attempt_count + ?,
                last_detail_attempt_at = CURRENT_TIMESTAMP,
                last_detail_error = ?,
                detail_verified = CASE WHEN ? THEN 1 ELSE detail_verified END,
                detail_verified_at = CASE
                  WHEN ? THEN CURRENT_TIMESTAMP
                  ELSE detail_verified_at
                END,
                pipeline_stage = ?,
                product_key = COALESCE(?, product_key),
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (
                int(increment_attempt),
                error_message,
                int(detail_verified),
                int(detail_verified),
                pipeline_stage,
                product_key,
                candidate_id,
            ),
        )


def record_discovery_title_alias_evidence(
    db_path: str | Path,
    *,
    candidate_id: int,
    source_title: str,
    alias: str,
    query: str,
    resolver: str,
    source_url: str,
    entity_id: str | None = None,
) -> None:
    """Store the public title mapping that enabled a fallback price query."""

    init_db(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO discovery_title_alias_evidence (
              candidate_id, source_title, alias, query, resolver, source_url, entity_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(candidate_id, alias, query) DO UPDATE SET
              source_title = excluded.source_title,
              resolver = excluded.resolver,
              source_url = excluded.source_url,
              entity_id = excluded.entity_id,
              updated_at = CURRENT_TIMESTAMP
            """,
            (
                candidate_id,
                source_title,
                alias,
                query,
                resolver,
                source_url,
                entity_id,
            ),
        )


def update_discovery_pool_last_scan(db_path: str | Path, pool_id: int) -> None:
    init_db(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            UPDATE discovery_pools
            SET last_scanned_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (pool_id,),
        )
        _refresh_discovery_pool_next_run(conn, pool_id)


def get_discovery_source_cooldown(db_path: str | Path, source: str) -> str | None:
    """Return the active source-wide cooldown deadline, if one exists."""

    init_db(db_path)
    normalized = source.strip().lower()
    if not normalized:
        raise ValueError("source is required")
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT cooldown_until
            FROM discovery_source_cooldowns
            WHERE source = ? AND datetime(cooldown_until) > CURRENT_TIMESTAMP
            """,
            (normalized,),
        ).fetchone()
    return str(row[0]) if row is not None else None


def set_discovery_source_cooldown(
    db_path: str | Path,
    source: str,
    *,
    cooldown_seconds: int,
    reason: str | None = None,
) -> str:
    """Persist the later of an existing and newly requested source cooldown."""

    init_db(db_path)
    normalized = source.strip().lower()
    if not normalized:
        raise ValueError("source is required")
    seconds = max(1, int(cooldown_seconds))
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO discovery_source_cooldowns (source, cooldown_until, reason)
            VALUES (?, datetime(CURRENT_TIMESTAMP, ?), ?)
            ON CONFLICT(source) DO UPDATE SET
              cooldown_until = CASE
                WHEN datetime(excluded.cooldown_until)
                   > datetime(discovery_source_cooldowns.cooldown_until)
                  THEN excluded.cooldown_until
                ELSE discovery_source_cooldowns.cooldown_until
              END,
              reason = excluded.reason,
              updated_at = CURRENT_TIMESTAMP
            """,
            (normalized, f"+{seconds} seconds", reason),
        )
    deadline = get_discovery_source_cooldown(db_path, normalized)
    if deadline is None:
        raise RuntimeError("source cooldown was not persisted")
    return deadline


def _refresh_discovery_pool_next_run(conn: sqlite3.Connection, pool_id: int) -> None:
    """Mirror the pool-wide rate limit onto the next-run timestamp for the UI."""

    conn.execute(
        """
        UPDATE discovery_pools
        SET next_run_at = CASE
          WHEN last_scanned_at IS NULL THEN CURRENT_TIMESTAMP
          ELSE datetime(last_scanned_at, '+' || scan_interval_minutes || ' minutes')
        END,
        updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (pool_id,),
    )


def upsert_discovery_candidate(db_path: str | Path, candidate: DiscoveryCandidate) -> int:
    init_db(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        row = _select_discovery_candidate_by_identity(
            conn, candidate.pool_id, candidate.identity_key
        )
        previous = _discovery_candidate_from_row(row) if row is not None else None
        candidate_id = _upsert_discovery_candidate(conn, candidate)
        _requeue_changed_verified_candidate(conn, previous, candidate, candidate_id)
        return candidate_id


def upsert_discovery_candidates_with_previous(
    db_path: str | Path, candidates: Iterable[DiscoveryCandidate]
) -> list[tuple[DiscoveryCandidate | None, int]]:
    """Upsert a source page in one transaction and retain pre-write state.

    Discovery needs the previous observation to decide whether a costly Xianyu
    lookup is necessary. Returning it alongside the resolved row id keeps that
    decision exact without opening one SQLite connection per visible card.
    """

    items = list(candidates)
    if not items:
        return []
    init_db(db_path)
    results: list[tuple[DiscoveryCandidate | None, int]] = []
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        for candidate in items:
            row = _select_discovery_candidate_by_identity(
                conn, candidate.pool_id, candidate.identity_key
            )
            previous = _discovery_candidate_from_row(row) if row is not None else None
            candidate_id = _upsert_discovery_candidate(conn, candidate)
            _requeue_changed_verified_candidate(conn, previous, candidate, candidate_id)
            results.append((previous, candidate_id))
    return results


def _requeue_changed_verified_candidate(
    conn: sqlite3.Connection,
    previous: DiscoveryCandidate | None,
    current: DiscoveryCandidate,
    candidate_id: int,
) -> None:
    """Require fresh source-detail evidence after an unverified card changes."""

    if (
        previous is None
        or not previous.detail_verified
        or previous.status != "active"
        or current.detail_verified
    ):
        return
    price_changed = abs(previous.source_price - current.source_price) > 0.001
    explicitly_reavailable = (
        current.availability == "available"
        and previous.availability != current.availability
    )
    if not price_changed and not explicitly_reavailable:
        return
    conn.execute(
        """
        UPDATE discovery_candidates
        SET detail_verified = 0,
            detail_verified_at = NULL,
            last_xianyu_checked_at = NULL,
            pipeline_stage = 'detail_queued',
            product_key = NULL,
            last_detail_error = NULL,
            availability = CASE
              WHEN ? THEN 'available'
              ELSE availability
            END,
            status = CASE
              WHEN ? THEN 'active'
              ELSE status
            END,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (
            int(explicitly_reavailable),
            int(explicitly_reavailable),
            candidate_id,
        ),
    )


def _upsert_discovery_candidate(
    conn: sqlite3.Connection, candidate: DiscoveryCandidate
) -> int:
    conn.execute(
        """
        INSERT INTO discovery_candidates (
          pool_id, media_type, identity_key, catalog_no, jan, title, artist,
          edition, source_item_id, source_url, source_image_url, source_price, source_currency,
          availability, status, observation_count, missing_scan_count,
          last_xianyu_checked_at, raw_text, detail_verified
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?)
        ON CONFLICT(pool_id, identity_key) DO UPDATE SET
          media_type = excluded.media_type,
          catalog_no = CASE WHEN discovery_candidates.detail_verified = 1
            AND excluded.detail_verified = 0 THEN discovery_candidates.catalog_no
            ELSE excluded.catalog_no END,
          jan = CASE WHEN discovery_candidates.detail_verified = 1
            AND excluded.detail_verified = 0 THEN discovery_candidates.jan
            ELSE excluded.jan END,
          title = CASE WHEN discovery_candidates.detail_verified = 1
            AND excluded.detail_verified = 0 THEN discovery_candidates.title
            ELSE excluded.title END,
          artist = CASE WHEN discovery_candidates.detail_verified = 1
            AND excluded.detail_verified = 0 THEN discovery_candidates.artist
            ELSE excluded.artist END,
          edition = CASE WHEN discovery_candidates.detail_verified = 1
            AND excluded.detail_verified = 0 THEN discovery_candidates.edition
            ELSE excluded.edition END,
          source_item_id = COALESCE(excluded.source_item_id, discovery_candidates.source_item_id),
          source_url = COALESCE(excluded.source_url, discovery_candidates.source_url),
          source_image_url = CASE WHEN discovery_candidates.detail_verified = 1
            AND excluded.detail_verified = 0 THEN discovery_candidates.source_image_url
            ELSE COALESCE(excluded.source_image_url, discovery_candidates.source_image_url) END,
          source_price = excluded.source_price,
          source_currency = excluded.source_currency,
          availability = CASE WHEN discovery_candidates.detail_verified = 1
            AND excluded.detail_verified = 0 THEN discovery_candidates.availability
            ELSE excluded.availability END,
          status = CASE WHEN discovery_candidates.detail_verified = 1
            AND excluded.detail_verified = 0 THEN discovery_candidates.status
            ELSE excluded.status END,
          observation_count = discovery_candidates.observation_count + 1,
          missing_scan_count = 0,
          raw_text = CASE WHEN discovery_candidates.detail_verified = 1
            AND excluded.detail_verified = 0 THEN discovery_candidates.raw_text
            ELSE excluded.raw_text END,
          detail_verified = MAX(discovery_candidates.detail_verified, excluded.detail_verified),
          pipeline_stage = CASE
            WHEN discovery_candidates.pipeline_stage = 'quarantined'
              AND excluded.detail_verified = 0 THEN 'search_discovered'
            ELSE discovery_candidates.pipeline_stage
          END,
          last_detail_error = CASE
            WHEN discovery_candidates.pipeline_stage = 'quarantined'
              AND excluded.detail_verified = 0 THEN NULL
            ELSE discovery_candidates.last_detail_error
          END,
          last_seen_at = CURRENT_TIMESTAMP,
          updated_at = CURRENT_TIMESTAMP
        """,
        (
            candidate.pool_id,
            candidate.media_type,
            candidate.identity_key,
            candidate.catalog_no,
            candidate.jan,
            candidate.title,
            candidate.artist,
            candidate.edition,
            candidate.source_item_id,
            candidate.source_url,
            candidate.source_image_url,
            candidate.source_price,
            candidate.source_currency,
            candidate.availability,
            candidate.status,
            candidate.missing_scan_count,
            candidate.last_xianyu_checked_at,
            candidate.raw_text,
            int(candidate.detail_verified),
        ),
    )
    row = conn.execute(
        "SELECT id FROM discovery_candidates WHERE pool_id = ? AND identity_key = ?",
        (candidate.pool_id, candidate.identity_key),
    ).fetchone()
    if row is None:
        raise RuntimeError("Discovery candidate insert did not resolve an id")
    return int(row[0])


def insert_discovery_opportunity(
    db_path: str | Path,
    opportunity: Opportunity,
    *,
    wameiji_item_id: int | None,
    discovery_candidate_id: int,
    media_type: str,
    identity_key: str,
    xianyu_price_sample_id: int | None = None,
) -> int:
    """Persist an evaluated automatic candidate and retain legacy compatibility."""
    opportunity_id = insert_opportunity(db_path, opportunity, wameiji_item_id=wameiji_item_id)
    with sqlite3.connect(db_path) as conn:
        # An evaluation is a point-in-time statement. Once the same source
        # listing is rechecked, its prior positive result must not remain on
        # the board beside (or after) a new reject.
        conn.execute(
            """
            UPDATE opportunities
            SET status = 'expired'
            WHERE discovery_candidate_id = ?
              AND id != ?
              AND COALESCE(status, 'active') = 'active'
            """,
            (discovery_candidate_id, opportunity_id),
        )
        conn.execute(
            """
            UPDATE opportunities
            SET discovery_candidate_id = ?, media_type = ?, identity_key = ?,
              xianyu_price_sample_id = ?, last_seen_at = CURRENT_TIMESTAMP,
              status = 'active'
            WHERE id = ?
            """,
            (
                discovery_candidate_id,
                media_type,
                identity_key,
                xianyu_price_sample_id,
                opportunity_id,
            ),
        )
    return opportunity_id


def list_discovery_opportunities(db_path: str | Path, limit: int = 50) -> list[dict[str, object]]:
    """Return current, detail-verified opportunities in the intended ranking.

    A board card is a live price comparison, not a permanent recommendation.
    Keep historical rows in SQLite but hide a card once its Xianyu evidence is
    older than the normal re-sample window.
    """
    init_db(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT
              o.id, o.expected_profit, o.net_margin, o.turnover_adjusted_roi,
              o.match_confidence, o.valid_xianyu_sample_count, o.liquidity_status,
              o.decision, o.xianyu_reference_price, o.expected_sale_price,
              o.landed_cost, o.last_seen_at,
              c.id AS candidate_id, c.media_type, c.identity_key,
              c.title AS candidate_title, c.catalog_no, c.jan, c.edition,
              c.source_url, c.source_price, c.source_currency,
              c.availability, c.status AS candidate_status, c.detail_verified,
              c.last_seen_at AS candidate_last_seen_at,
              c.source_price AS purchase_price_jpy,
              o.xianyu_reference_price AS xianyu_price_cny,
              o.valid_xianyu_sample_count AS xianyu_sample_rows,
              m.title AS item_title,
              COALESCE(NULLIF(c.source_image_url, ''), NULLIF(m.image_url, '')) AS image_url,
              COALESCE(m.url, c.source_url) AS url,
              CASE WHEN xs.id IS NOT NULL THEN 'xianyu' END AS xianyu_source,
              CASE WHEN xs.id IS NOT NULL THEN 'goofish' END AS xianyu_source_site,
              xs.title AS xianyu_item_title,
              xs.price_cny AS xianyu_display_price_cny,
              xs.url AS xianyu_url,
              xs.image_url AS xianyu_image_url,
              CASE WHEN xs.id IS NOT NULL THEN 'available' END AS xianyu_availability
            FROM opportunities o
            JOIN discovery_candidates c ON c.id = o.discovery_candidate_id
            JOIN discovery_pools p ON p.id = c.pool_id
            LEFT JOIN market_items m ON m.id = o.wameiji_item_id
            LEFT JOIN xianyu_price_samples xs ON xs.id = o.xianyu_price_sample_id
            WHERE c.status = 'active' AND c.detail_verified = 1
              AND COALESCE(o.status, 'active') = 'active'
              AND NULLIF(TRIM(COALESCE(c.source_image_url, m.image_url)), '') IS NOT NULL
              AND NULLIF(TRIM(xs.image_url), '') IS NOT NULL
              AND datetime(c.detail_verified_at) >= datetime(CURRENT_TIMESTAMP, ?)
              AND datetime(o.last_seen_at) >= datetime(CURRENT_TIMESTAMP, ?)
              AND o.decision != 'reject'
              AND o.expected_profit >= p.min_profit_cny
              AND o.net_margin >= p.min_margin
              AND o.match_confidence >= p.min_match_confidence
              AND o.valid_xianyu_sample_count >= p.min_valid_xianyu_samples
            ORDER BY o.expected_profit DESC, o.match_confidence DESC,
              o.valid_xianyu_sample_count DESC, c.last_seen_at DESC, o.id DESC
            LIMIT ?
            """,
            (
                f"-{_DISCOVERY_SOURCE_DETAIL_FRESHNESS_MINUTES} minutes",
                f"-{_DISCOVERY_OPPORTUNITY_FRESHNESS_MINUTES} minutes",
                max(1, int(limit)),
            ),
        ).fetchall()
    return [
        dict(row)
        for row in rows
        if is_usable_product_image(row["image_url"])
        and is_usable_product_image(row["xianyu_image_url"])
    ]


def discovery_summary(db_path: str | Path) -> dict[str, object]:
    init_db(db_path)
    freshness_window = f"-{_DISCOVERY_OPPORTUNITY_FRESHNESS_MINUTES} minutes"
    source_freshness_window = f"-{_DISCOVERY_SOURCE_DETAIL_FRESHNESS_MINUTES} minutes"
    visible_opportunities = list_discovery_opportunities(db_path, limit=10_000)
    active_opportunities = len(visible_opportunities)
    highest_profit = max(
        (float(opportunity["expected_profit"] or 0) for opportunity in visible_opportunities),
        default=0.0,
    )
    total_profit = sum(
        float(opportunity["expected_profit"] or 0) for opportunity in visible_opportunities
    )
    with sqlite3.connect(db_path) as conn:
        active_candidates = conn.execute(
            """
            SELECT COUNT(*) FROM discovery_candidates
            WHERE status = 'active' AND detail_verified = 1
            """
        ).fetchone()[0]
        detail_pipeline = conn.execute(
            """
            SELECT
              SUM(CASE
                WHEN datetime(detail_verified_at) >= datetime(CURRENT_TIMESTAMP, ?)
                THEN 1 ELSE 0
              END) AS fresh_source_details,
              SUM(CASE
                WHEN detail_verified_at IS NULL
                  OR datetime(detail_verified_at) < datetime(CURRENT_TIMESTAMP, ?)
                THEN 1 ELSE 0
              END) AS stale_source_details,
              SUM(CASE
                WHEN datetime(detail_verified_at) >= datetime(CURRENT_TIMESTAMP, ?)
                  AND (
                    last_xianyu_checked_at IS NULL
                    OR datetime(last_xianyu_checked_at) <= datetime(CURRENT_TIMESTAMP, ?)
                  )
                THEN 1 ELSE 0
              END) AS resale_ready_candidates
            FROM discovery_candidates
            WHERE status = 'active' AND detail_verified = 1
            """,
            (
                source_freshness_window,
                source_freshness_window,
                source_freshness_window,
                freshness_window,
            ),
        ).fetchone()
        last_scan_at = conn.execute(
            "SELECT MAX(finished_at) FROM discovery_runs WHERE status = 'ok'"
        ).fetchone()[0]
        login_state_row = conn.execute(
            "SELECT value, updated_at FROM user_settings WHERE key = ?",
            (_DISCOVERY_XIANYU_LOGIN_STATE_KEY,),
        ).fetchone()
    xianyu_login_state = "unknown"
    xianyu_login_checked_at = None
    if login_state_row is not None:
        candidate_state = str(login_state_row[0]).strip()
        if candidate_state in {"ready", "login_required"}:
            xianyu_login_state = candidate_state
            xianyu_login_checked_at = login_state_row[1]
    return {
        "active_candidates": int(active_candidates or 0),
        "fresh_source_details": int(detail_pipeline[0] or 0),
        "stale_source_details": int(detail_pipeline[1] or 0),
        "resale_ready_candidates": int(detail_pipeline[2] or 0),
        "active_opportunities": int(active_opportunities or 0),
        "total_expected_profit": float(total_profit or 0),
        "highest_expected_profit": float(highest_profit or 0),
        "last_scan_at": last_scan_at,
        "xianyu_login_state": xianyu_login_state,
        "xianyu_login_checked_at": xianyu_login_checked_at,
    }


def list_discovery_runs(db_path: str | Path, limit: int = 30) -> list[dict[str, object]]:
    init_db(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT id, pool_id, keyword_id, keyword, source, status,
              discovered_count, candidate_count, detail_query_count,
              detail_verified_count, detail_rejected_count, evaluated_count,
              xianyu_query_count, resale_sampled_count, error_type, error_message,
              screenshot_path, raw_snapshot_path, started_at, finished_at
            FROM discovery_runs
            ORDER BY id DESC LIMIT ?
            """,
            (max(1, int(limit)),),
        ).fetchall()
    return [dict(row) for row in rows]


def create_collector_command(
    db_path: str | Path,
    command_type: str,
    payload: dict[str, object] | None = None,
    *,
    dedupe_key: str | None = None,
) -> dict[str, object]:
    init_db(db_path)
    serialized = json.dumps(payload or {}, ensure_ascii=False, sort_keys=True)
    # The SQLite row id restarts when Render's free instance is rebuilt.  Give
    # every command a stable opaque identity so the collector can mirror it
    # safely across those restarts.
    remote_command_id = uuid4().hex
    with sqlite3.connect(db_path) as conn:
        try:
            cursor = conn.execute(
                """
                INSERT INTO collector_commands (
                  remote_command_id, command_type, payload_json, dedupe_key
                ) VALUES (?, ?, ?, ?)
                """,
                (remote_command_id, command_type, serialized, dedupe_key),
            )
            command_id = int(cursor.lastrowid)
        except sqlite3.IntegrityError:
            row = conn.execute(
                """
                SELECT id FROM collector_commands
                WHERE dedupe_key = ? AND status IN ('pending', 'accepted', 'running')
                ORDER BY id DESC LIMIT 1
                """,
                (dedupe_key,),
            ).fetchone()
            if row is None:
                raise
            command_id = int(row[0])
    return get_collector_command(db_path, command_id)


def get_collector_command(db_path: str | Path, command_id: int) -> dict[str, object]:
    init_db(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """
            SELECT id, remote_command_id, command_type, payload_json, status, result_json, dedupe_key,
              created_at, accepted_at, completed_at, updated_at
            FROM collector_commands WHERE id = ?
            """,
            (command_id,),
        ).fetchone()
    if row is None:
        raise KeyError(f"Collector command not found: {command_id}")
    return _collector_command_row(row)


def list_collector_commands(
    db_path: str | Path,
    *,
    statuses: tuple[str, ...] | None = None,
    limit: int = 50,
) -> list[dict[str, object]]:
    init_db(db_path)
    sql = """
        SELECT id, remote_command_id, command_type, payload_json, status, result_json, dedupe_key,
          created_at, accepted_at, completed_at, updated_at
        FROM collector_commands
    """
    params: list[object] = []
    if statuses:
        sql += " WHERE status IN (" + ", ".join("?" for _ in statuses) + ")"
        params.extend(statuses)
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(max(1, int(limit)))
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(sql, tuple(params)).fetchall()
    return [_collector_command_row(row) for row in rows]


def upsert_remote_collector_command(
    db_path: str | Path, command: dict[str, object]
) -> dict[str, object]:
    """Mirror a Render command into the source database before it is handled.

    The source database is later copied back to Render.  Retaining the remote
    command's identity here prevents a successful acknowledgement from being
    lost during that next copy.
    """
    # New Render commands carry a UUID which remains unique even if Render's
    # temporary command database is recreated.  Keep the numeric-id fallback
    # so an already-running older collector can still be mirrored.
    remote_id = command.get("remote_command_id") or command.get("id")
    if remote_id is None or str(remote_id).strip() == "":
        raise ValueError("remote_command_id_required")
    command_type = str(command.get("command_type") or "").strip()
    if not command_type:
        raise ValueError("command_type_required")
    payload = command.get("payload")
    result = command.get("result")
    status = str(command.get("status") or "pending")
    if status not in {"pending", "accepted", "running", "completed", "human_required", "failed"}:
        status = "pending"
    remote_key = str(remote_id).strip()
    payload_json = json.dumps(payload if isinstance(payload, dict) else {}, ensure_ascii=False, sort_keys=True)
    result_json = json.dumps(result if isinstance(result, dict) else {}, ensure_ascii=False, sort_keys=True)
    init_db(db_path)
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT id, status FROM collector_commands WHERE remote_command_id = ?",
            (remote_key,),
        ).fetchone()
        if row is None:
            cursor = conn.execute(
                """
                INSERT INTO collector_commands (
                  remote_command_id, command_type, payload_json, status, result_json, dedupe_key
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (remote_key, command_type, payload_json, status, result_json, f"remote:{remote_key}"),
            )
            command_id = int(cursor.lastrowid)
        else:
            command_id = int(row[0])
            # A retrying Render poll must never turn a locally completed
            # command back into pending before its source DB is replicated.
            conn.execute(
                """
                UPDATE collector_commands
                SET command_type = ?, payload_json = ?,
                  status = CASE
                    WHEN status IN ('completed', 'human_required', 'failed') THEN status
                    ELSE ?
                  END,
                  result_json = CASE
                    WHEN status IN ('completed', 'human_required', 'failed') THEN result_json
                    ELSE ?
                  END,
                  updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (command_type, payload_json, status, result_json, command_id),
            )
    return get_collector_command(db_path, command_id)


def complete_collector_command(
    db_path: str | Path,
    command_id: int,
    result: dict[str, object] | None = None,
    *,
    status: str = "completed",
) -> dict[str, object]:
    if status not in {"accepted", "running", "completed", "human_required", "failed"}:
        raise ValueError(f"Unsupported collector command status: {status}")
    init_db(db_path)
    result_json = json.dumps(result or {}, ensure_ascii=False, sort_keys=True)
    with sqlite3.connect(db_path) as conn:
        cursor = conn.execute(
            """
            UPDATE collector_commands
            SET status = ?, result_json = ?,
              accepted_at = CASE WHEN ? IN ('accepted', 'running') THEN CURRENT_TIMESTAMP ELSE accepted_at END,
              completed_at = CASE WHEN ? IN ('completed', 'human_required', 'failed') THEN CURRENT_TIMESTAMP ELSE completed_at END,
              updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (status, result_json, status, status, command_id),
        )
        if cursor.rowcount == 0:
            raise KeyError(f"Collector command not found: {command_id}")
    return get_collector_command(db_path, command_id)


def _collector_command_row(row: sqlite3.Row) -> dict[str, object]:
    payload = dict(row)
    for field in ("payload_json", "result_json"):
        raw = payload.pop(field, None)
        try:
            payload["payload" if field == "payload_json" else "result"] = json.loads(raw or "{}")
        except (TypeError, json.JSONDecodeError):
            payload["payload" if field == "payload_json" else "result"] = {}
    return payload


def get_opportunity(db_path: str | Path, opportunity_id: int) -> Opportunity:
    init_db(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """
            SELECT
              o.*,
              m.source, m.source_site, m.external_item_id, m.catalog_no AS item_catalog_no,
              m.jan, m.title, m.price, m.currency, m.price_cny_display,
              m.japan_domestic_shipping_jpy, m.proxy_fee_jpy, m.fees_hint, m.url, m.image_url,
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
        japan_domestic_shipping_jpy=row["japan_domestic_shipping_jpy"],
        proxy_fee_jpy=row["proxy_fee_jpy"],
        fees_hint=row["fees_hint"],
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
          price_cny_display, japan_domestic_shipping_jpy, proxy_fee_jpy, fees_hint,
          url, image_url, availability, condition_text, raw_text
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
            japan_domestic_shipping_jpy=row["japan_domestic_shipping_jpy"],
            proxy_fee_jpy=row["proxy_fee_jpy"],
            fees_hint=row["fees_hint"],
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
