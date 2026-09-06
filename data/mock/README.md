# Synthetic offline demo fixtures

这两个 JSON 文件是 2026-09-06 接手交接包时新建的**合成离线测试样本**。
交接包中原有的 `data/mock/` 文件缺失，本目录不是恢复的原件，也不是历史市场数据。
商品、标题、货号关联、价格、可购买状态均为人为设计，不能作为真实商品、市场价格、
利润或采购判断的证据。`SRCL-3520` 仅保留为现有测试使用的检索编号。

全部链接使用 `https://example.invalid/`，不会指向真实商品。
商品行保留 `source: "mock"` 和 `source_site: "mock"`；
`XianyuPriceSample` 模型不支持独立的 `source` 字段，因此其 `raw_text` 保留
`source=mock` 标签，标题同时带 `[synthetic]` 标识。

## 样本内容

- `wameiji_items.sample.json`：1 条商品，测试价格 1200 JPY，标题以 `Artist Album` 开头。
- `xianyu_samples.sample.json`：7 条记录；3 条普通价格为 260、280、270 CNY，
  其中两条标为初回限定，一条标为通常盤；另 4 条覆盖 1 CNY 极低价、9999 CNY
  极高价、求购和空盒噪声。
- 默认清洗后保留 3 条，参考价为 270 CNY。默认评估可以生成正常提醒机会。
- 将价格下限设为 300 CNY 后，普通记录全部低于下限；其余记录仍属于极高价或噪声。
  把议价和流动性折扣调到 0.5 × 0.8 后，预期收益不足以覆盖示范成本。

## 清洗契约

指定版本时，标题明确写出另一版本（例如 `通常盤` 对 `初回限定`）的样本会被标记为
`invalid_edition_mismatch`。少于 3 条有效样本只保留人工复核所需的原始记录，参考价为
0 且流动性为 `poor`；3 条及以上才计算中位数或截尾均值。

## 2026-09-06 验证记录

使用本项目 `.venv` 的 Python 3.11.9 执行以下 8 个测试文件，共 13 项：

```powershell
.\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_mock_scan.py tests/test_mock_filtering.py tests/test_scan_service.py tests/test_cli_config_affects_scan.py tests/test_cli_config_xianyu_discount.py tests/test_cli_config_and_listing.py tests/test_cli.py tests/test_cli_doctor.py -q --tb=short
```

结果：**13 passed**。版本筛选和低样本流动性断言已与上述契约一致。

另已回读两个 JSON 并检查所有 URL 域名和 synthetic/mock 标签；默认评估得到
`strong_alert`，7 条输入、3 条有效记录、参考价 270 CNY、示范到岸成本 103.07 CNY、
示范利润 88.59 CNY、匹配置信度 0.93、流动性 `thin`。这些数值仅验证离线计算链路。
