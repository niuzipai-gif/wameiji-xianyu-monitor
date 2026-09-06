#!/usr/bin/env python3
import sqlite3, re, sys

DB = sys.argv[1] if len(sys.argv) > 1 else "/var/lib/cd_monitor/cd_monitor.db"

NON_CD_STRICT = [
    "T恤", "短袖", "长袖", "外套", "卫衣", "裤", "裙子",
    "球衣", "尺码", "码子", "均码", "S码", "M码", "L码", "XL码", "XXL码",
    "优衣库", "Uniqlo", "Kith", "Supreme", "联名T", "联名短",
    "cos服", "cos大全套", "cos全套", "cos假发", "cosplay",
    "手办", "景品", "figma", "粘土人", "毛绒", "玩偶",
    "吧唧", "亚克力", "立牌", "徽章", "明信片", "色纸",
    "卡套", "小卡", "票根", "透卡", "场贩", "谷子",
    "绘本", "漫画", "小说", "旧书", "正版旧书",
    "光腿神器", "马油",
    "教学", "教材", "教辅",
    "代付", "代注册", "代入会",
    "耳机仓", "手机壳", "手机链", "键盘", "鼠标垫",
    "麻将", "扑克", "拼豆", "TC卡", "PT卡", "对战搭档",
    "黑胶唱片",
]

NON_CD_RE = re.compile("|".join(NON_CD_STRICT))


def is_bad(title):
    if not title:
        return True, "empty"
    m = NON_CD_RE.search(title)
    if m:
        return True, "non_cd"
    return False, ""


def clean_xps(con):
    rows = con.execute("SELECT id, title FROM xianyu_price_samples WHERE is_valid=1").fetchall()
    n = 0
    rs = {}
    for sid, t in rows:
        b, r = is_bad(t or "")
        if b:
            n += 1
            rs[r] = rs.get(r, 0) + 1
            con.execute(
                "UPDATE xianyu_price_samples SET is_valid=0, invalid_reason=? WHERE id=?",
                ("cleanup_" + r, sid),
            )
    return n, rs


def clean_mi(con):
    rows = con.execute("SELECT id, title FROM market_items WHERE source='xianyu'").fetchall()
    n = 0
    rs = {}
    for mid, t in rows:
        b, r = is_bad(t or "")
        if b:
            n += 1
            rs[r] = rs.get(r, 0) + 1
            con.execute("UPDATE market_items SET catalog_no='_BAD_' WHERE id=?", (mid,))
    return n, rs


def main():
    con = sqlite3.connect(DB, timeout=60)
    n1, r1 = clean_xps(con)
    print("xps invalid:", n1, r1)
    n2, r2 = clean_mi(con)
    print("mi reassigned:", n2, r2)
    con.commit()
    nt = con.execute("SELECT count(*) FROM xianyu_price_samples").fetchone()[0]
    nv = con.execute("SELECT count(*) FROM xianyu_price_samples WHERE is_valid=1").fetchone()[0]
    nb = con.execute("SELECT count(*) FROM market_items WHERE source='xianyu' AND catalog_no='_BAD_'").fetchone()[0]
    nm = con.execute("SELECT count(*) FROM market_items WHERE source='xianyu'").fetchone()[0]
    pct_v = round(nv * 100.0 / nt, 1) if nt else 0
    pct_b = round(nb * 100.0 / nm, 1) if nm else 0
    print("xps valid:", nv, "/", nt, "(", pct_v, "%)")
    print("mi bad:", nb, "/", nm, "(", pct_b, "%)")
    con.close()


if __name__ == "__main__":
    main()
