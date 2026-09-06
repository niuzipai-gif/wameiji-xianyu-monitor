# Full Handoff Package

更新时间：2026-06-15

这是“毫无保留”交接包说明。它不同于 `output/WAMEIJI-XIANYU-handoff.zip` 精简交接包。

## 包含内容

完整交接包会包含：

- 源码：`src/`、`cd_monitor/`
- 测试：`tests/`
- 前端：`web/`
- 脚本：`scripts/`
- 工具目录：`tools/`
- 配置和项目文件：`pyproject.toml`、`.env.example`、`config.example.yaml`
- 项目规格与执行记录：`CODEX_PROJECT_SPEC*.md`、`CODEX_MASTER_PROMPT*.txt`
- 交接文档：`README.md`、`DELIVERY_REPORT.md`、`HANDOFF_TO_CLAUDE_CODE.md`、`CLAUDE_CODE_TODO.md`、`EXTERNAL_COLLECTOR_CONTRACT.md`、`COMMUNITY_RESEARCH.md`
- 运行态数据：`data/`
- 已生成交接包快照：`output/handoff/` 和 `output/WAMEIJI-XIANYU-handoff.zip`

## 敏感性

这个完整包会包含运行态文件，例如：

- `data/web-access-token.txt`
- `data/public-url.txt`
- SQLite 数据库
- 隧道日志
- mock / snapshot / output 数据

因此该包应视为内部敏感资料，不应上传公开仓库，不应直接发给不可信第三方。

## 不保证包含

为了避免无限递归和无意义膨胀，完整包不会包含：

- `.pytest_cache/`
- `.ruff_cache/`
- `__pycache__/`
- 当前正在生成的 `output/full_handoff/`
- 当前正在生成的完整 zip 本身

## 后续接手建议

1. 先读 `HANDOFF_TO_CLAUDE_CODE.md`。
2. 再读 `CLAUDE_CODE_TODO.md`。
3. 如果要替换真实数据采集层，按 `EXTERNAL_COLLECTOR_CONTRACT.md` 输出标准 HTML/JSON/CSV/状态文件。
4. 当前主体系统继续负责入库、评估、展示、告警、报告和复核。
