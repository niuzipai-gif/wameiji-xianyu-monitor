SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS watchlist (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  catalog_no TEXT NOT NULL,
  catalog_no_compact TEXT,
  jan TEXT,
  artist TEXT,
  title_jp TEXT,
  title_cn TEXT,
  edition TEXT,
  required_keywords TEXT,
  excluded_keywords TEXT,
  priority INTEGER DEFAULT 1,
  enabled INTEGER DEFAULT 1,
  scan_interval_minutes INTEGER DEFAULT 60,
  expected_holding_days INTEGER DEFAULT 30,
  min_margin REAL DEFAULT 0.30,
  min_diff REAL DEFAULT 1500,
  notify_channel TEXT DEFAULT 'none',
  platform TEXT DEFAULT 'both',
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS search_runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  source TEXT NOT NULL,
  keyword TEXT NOT NULL,
  status TEXT NOT NULL,
  started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  finished_at TIMESTAMP,
  error_type TEXT,
  error_message TEXT,
  screenshot_path TEXT,
  raw_snapshot_path TEXT
);

CREATE TABLE IF NOT EXISTS market_items (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  source TEXT NOT NULL,
  source_site TEXT,
  external_item_id TEXT,
  catalog_no TEXT,
  jan TEXT,
  title TEXT NOT NULL,
  price REAL NOT NULL,
  currency TEXT NOT NULL,
  price_cny_display REAL,
  japan_domestic_shipping_jpy REAL,
  proxy_fee_jpy REAL,
  fees_hint TEXT,
  url TEXT,
  image_url TEXT,
  availability TEXT,
  condition_text TEXT,
  raw_text TEXT,
  match_confidence REAL,
  fetched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS xianyu_price_samples (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  catalog_no TEXT NOT NULL,
  title TEXT NOT NULL,
  price_cny REAL NOT NULL,
  url TEXT,
  image_url TEXT,
  seller_text TEXT,
  raw_text TEXT,
  is_valid INTEGER DEFAULT 1,
  invalid_reason TEXT,
  fetched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS listing_observations (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  source TEXT NOT NULL CHECK(source IN ('wameiji', 'xianyu')),
  source_listing_id TEXT NOT NULL,
  canonical_product_key TEXT,
  title TEXT NOT NULL,
  price REAL NOT NULL,
  currency TEXT NOT NULL,
  url TEXT NOT NULL,
  image_url TEXT NOT NULL,
  availability TEXT NOT NULL,
  condition_group TEXT NOT NULL,
  completeness TEXT NOT NULL,
  evidence_level TEXT NOT NULL CHECK(evidence_level IN ('search_card', 'detail_verified')),
  raw_snapshot_path TEXT,
  screenshot_path TEXT,
  source_detail_fee REAL,
  captured_at TIMESTAMP NOT NULL,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(source, source_listing_id, captured_at)
);

CREATE INDEX IF NOT EXISTS idx_listing_observations_product
ON listing_observations(canonical_product_key, source, captured_at DESC);

CREATE TABLE IF NOT EXISTS price_comparisons (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  canonical_product_key TEXT NOT NULL,
  wameiji_observation_id INTEGER NOT NULL REFERENCES listing_observations(id),
  xianyu_observation_id INTEGER NOT NULL REFERENCES listing_observations(id),
  cost_config_json TEXT NOT NULL,
  landed_cost_cny REAL,
  sale_price_cny REAL NOT NULL,
  expected_profit_cny REAL,
  net_margin REAL,
  status TEXT NOT NULL CHECK(status IN ('eligible', 'below_margin', 'cost_pending')),
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_price_comparisons_product_time
ON price_comparisons(canonical_product_key, created_at DESC);

CREATE TABLE IF NOT EXISTS opportunities (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  catalog_no TEXT NOT NULL,
  wameiji_item_id INTEGER,
  xianyu_reference_price REAL,
  expected_sale_price REAL,
  landed_cost REAL,
  expected_revenue REAL,
  expected_profit REAL,
  net_margin REAL,
  turnover_adjusted_roi REAL,
  match_confidence REAL,
  valid_xianyu_sample_count INTEGER,
  liquidity_status TEXT,
  decision TEXT,
  risk_labels TEXT,
  opportunity_hash TEXT UNIQUE,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY(wameiji_item_id) REFERENCES market_items(id)
);

CREATE TABLE IF NOT EXISTS sent_alerts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  opportunity_id INTEGER NOT NULL,
  alert_hash TEXT UNIQUE NOT NULL,
  channel TEXT NOT NULL,
  status TEXT NOT NULL,
  sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  response_text TEXT,
  FOREIGN KEY(opportunity_id) REFERENCES opportunities(id)
);

CREATE TABLE IF NOT EXISTS review_decisions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  opportunity_id INTEGER NOT NULL,
  result TEXT NOT NULL,
  note TEXT,
  reviewed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY(opportunity_id) REFERENCES opportunities(id)
);

CREATE TABLE IF NOT EXISTS candidate_rechecks (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  opportunity_id INTEGER NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending',
  reason TEXT,
  scheduled_at TEXT NOT NULL,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY(opportunity_id) REFERENCES opportunities(id)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_candidate_rechecks_one_pending
ON candidate_rechecks(opportunity_id)
WHERE status = 'pending';
"""
