p = r'src\cd_monitor\web_server.py'
with open(p, 'r', encoding='utf-8') as f:
    src = f.read()

old = '''        ).fetchone()[\"c\"]
        rows = conn.execute(
            \"\"\"
            SELECT
              o.id, o.catalog_no, o.xianyu_reference_price, o.expected_sale_price,
              o.expected_profit, o.net_margin, o.turnover_adjusted_roi,
              o.match_confidence, o.valid_xianyu_sample_count, o.liquidity_status,
              o.decision, o.risk_labels, o.created_at,
              m.source AS wameiji_source, m.source_site AS wameiji_source_site,
              m.title AS item_title, m.price AS purchase_price_jpy, m.url, m.image_url,
              m.availability, m.condition_text,
              xm.source AS xianyu_source, xm.source_site AS xianyu_source_site,
              xm.title AS xianyu_item_title, xm.price AS xianyu_price_cny,
              xm.url AS xianyu_url, xm.image_url AS xianyu_image_url,
              xm.availability AS xianyu_availability,
              (SELECT COUNT(*) FROM xianyu_price_samples xps WHERE xps.catalog_no = o.catalog_no) AS xianyu_sample_rows
            FROM opportunities o
            LEFT JOIN market_items m ON m.id = o.wameiji_item_id
            LEFT JOIN market_items xm ON xm.id = (
              SELECT xmi.id FROM market_items xmi
              WHERE xmi.catalog_no = o.catalog_no AND xmi.source = '\\''xianyu'\\''
              ORDER BY xmi.fetched_at DESC, xmi.id DESC
              LIMIT 1
            )
            ORDER BY RANDOM()
            LIMIT ? OFFSET ?
            \"\"\",
            (limit, offset),
        ).fetchall()'''
new = '''        ).fetchone()[\"c\"]
        order_clause = \"ORDER BY RANDOM()\" if randomize else \"ORDER BY o.id DESC\"
        rows = conn.execute(
            f\"\"\"
            SELECT
              o.id, o.catalog_no, o.xianyu_reference_price, o.expected_sale_price,
              o.expected_profit, o.net_margin, o.turnover_adjusted_roi,
              o.match_confidence, o.valid_xianyu_sample_count, o.liquidity_status,
              o.decision, o.risk_labels, o.created_at,
              m.source AS wameiji_source, m.source_site AS wameiji_source_site,
              m.title AS item_title, m.price AS purchase_price_jpy, m.url, m.image_url,
              m.availability, m.condition_text,
              xm.source AS xianyu_source, xm.source_site AS xianyu_source_site,
              xm.title AS xianyu_item_title, xm.price AS xianyu_price_cny,
              xm.url AS xianyu_url, xm.image_url AS xianyu_image_url,
              xm.availability AS xianyu_availability,
              (SELECT COUNT(*) FROM xianyu_price_samples xps WHERE xps.catalog_no = o.catalog_no) AS xianyu_sample_rows
            FROM opportunities o
            LEFT JOIN market_items m ON m.id = o.wameiji_item_id
            LEFT JOIN market_items xm ON xm.id = (
              SELECT xmi.id FROM market_items xmi
              WHERE xmi.catalog_no = o.catalog_no AND xmi.source = '\\''xianyu'\\''
              ORDER BY xmi.fetched_at DESC, xmi.id DESC
              LIMIT 1
            )
            {order_clause}
            LIMIT ? OFFSET ?
            \"\"\",
            (limit, offset),
        ).fetchall()'''
# Need to write to file matching exactly
with open('old.txt','w',encoding='utf-8') as f: f.write(old)
with open('new.txt','w',encoding='utf-8') as f: f.write(new)
print('chars old:', len(old))
print('chars new:', len(new))
print('exists:', old in src)
