# External Collector Contract

更新时间：2026-06-15

本文件定义外部数据采集层与当前 CD Monitor 系统的对接格式。当前系统负责入库、解析、机会计算、Web 展示、通知、报告和复核；外部采集层只需要产出标准文件。

## 推荐目录

```text
data/external/
  SRCL-3520/
    wameiji.html
    xianyu.html
    wameiji.json
    xianyu.json
    evidence/
      wameiji.png
      xianyu.png
      network.json
```
主要方式参考这个项目：https://github.com/Usagi-org/ai-goofish-monitor（闲鱼侧的所有功能直接使用这个项目，不许有多余创新，挖煤姬侧的功能也是以这个为参考来做）
结合agent情况参考这些项目：https://github.com/LENKIN233/xianyu-monitor-skill、https://github.com/fancyboi999/goofish-cli、https://github.com/fancyboi999/goofish-cli

备用：
## 方式一：HTML 快照

Wameiji HTML：

```powershell
python -m cd_monitor.cli evaluate-html --catalog-no SRCL-3520 --wameiji-html data/external/SRCL-3520/wameiji.html --xianyu-html data/external/SRCL-3520/xianyu.html --db data/cd_monitor.db --snapshot-dir data/snapshots
```

要求：

- HTML 文件应是用户最终可见搜索结果页或结果片段。
- 应尽量保留标题、价格、链接、图片、来源站点、售卖状态、品相文本。
- 如果采集端遇到登录、验证码、安全验证、风控页，应不要输出商品假数据，而应输出状态文件或空文件，并让人工处理。

## 方式二：统一 JSON

可使用 `evaluate-json` 读取结构化 JSON。

示例：

```json
{
  "catalog_no": "SRCL-3520",
  "wameiji": [
    {
      "title": "Artist SRCL-3520 初回限定",
      "price": 1200,
      "currency": "JPY",
      "source_site": "mercari",
      "url": "https://example.invalid/item/1",
      "image_url": "https://example.invalid/image.jpg",
      "availability": "available",
      "condition_text": "盤傷なし",
      "raw_text": "visible source text"
    }
  ],
  "xianyu": [
    {
      "title": "Artist SRCL-3520 初回限定",
      "price_cny": 260,
      "url": "https://www.goofish.com/item?id=1",
      "image_url": "https://example.invalid/x.jpg",
      "raw_text": "visible source text"
    }
  ]
}
```

字段约定：

- `catalog_no`: 必填，品番。
- `wameiji[].title`: 必填。
- `wameiji[].price`: 必填，日元数字。
- `wameiji[].currency`: 建议为 `JPY`。
- `wameiji[].availability`: 可选，建议值 `available` / `sold_out` / `reserved` / `unknown_but_visible`。
- `xianyu[].title`: 必填。
- `xianyu[].price_cny`: 必填，人民币数字。
- `url`、`image_url`、`raw_text` 建议保留，便于人工复核和报告展示。

## 方式三：闲鱼 CSV

闲鱼侧可导出 CSV，然后使用：

```powershell
python -m cd_monitor.cli import-csv --catalog-no SRCL-3520 --csv data/external/SRCL-3520/xianyu.csv --db data/cd_monitor.db
```

建议列：

```text
catalog_no,title,price_cny,url,image_url,raw_text
```

## 状态文件

如果外部采集器没有产出商品，应写一个状态 JSON 方便排查：

```json
{
  "catalog_no": "SRCL-3520",
  "source": "xianyu",
  "status": "human_required",
  "error_type": "security_check",
  "error_message": "login, captcha, security check, or rate limit page detected",
  "captured_at": "2026-06-15T00:00:00+08:00",
  "evidence": {
    "html": "data/external/SRCL-3520/xianyu.html",
    "screenshot": "data/external/SRCL-3520/evidence/xianyu.png",
    "network": "data/external/SRCL-3520/evidence/network.json"
  }
}
```

## 当前系统消费入口

- HTML：`evaluate-html`
- JSON：`evaluate-json`
- CSV：`import-csv`
- Web UI：设置页和导入区支持粘贴/上传快照后评估

## 输出原则

- 不要伪造商品。
- 不要把多个商品混成一个卡片。
- 明确不可购买或已售出的商品要保留状态，系统会降低或拒绝机会。
- 保留原始文本和证据路径，便于人工复核。
