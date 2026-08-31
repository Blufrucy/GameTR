# ADR-0005：AI 结构化输出可靠性策略

- 状态：已定稿（2026-08-28 DeepSeek v4-flash 实测）
- 日期：2026-08-27（初稿）、2026-08-28（实测定稿）
- 来源：M0 Spike 3（tests/spikes/spike3_ai/）
- 相关：M3 的 Provider 层、Validator、占位符保护器

## 背景（Context）

翻译流水线让模型批量返回 JSON 数组 `[{"id","translation"}]`。模型输出不稳定（非法 JSON、占位符丢失/乱序、字段缺失）会直接污染条目库。M0 需要实测三种失败率，决定三件事：
1. 是否必须 `response_format: json_schema`
2. 占位符保护/还原在流水线的地位
3. 重试与校验策略

## 决策（Decision）

**策略分两层，无论实测结果如何都是硬要求：**

1. **占位符保护器是流水线必须阶段**（不是"可选项"）：发请求前把 `\C[n] \I[n] \N[n] \V[n] \G \{ \. \^ \| \!` 替换为 `⟦n⟧`，收到后按 `⟦n⟧` 序列（数量+顺序+编号）逐条校验，失败即重试/标 warning。Spike 3 已实现对 48 条含占位符样本的保护→还原→检测自洽（tests/spikes/spike3_ai 自检通过）。
2. **Provider 优先用 `response_format: json_schema`**，端点为 OpenAI 兼容时默认开启；不支持的端点降级 json_object，再降级普通 JSON。

**待实测数据定稿的决策**（判定阈值，路线图 1.5 预设；2026-08-28 已按实测定稿）：

| 指标 | 阈值 | 实测（DeepSeek v4-flash） | 触发动作（定稿） |
| :--- | :--- | :--- | :--- |
| 非法JSON率 | >2% | **默认 33.33% / json_object 0%** | response_format 必须开启（不可降级）→ **触发** |
| 占位符破坏率 | >1% | 0%（48/59 样本含占位符） | 破坏条目强制重试 1 次后标 warning → **未触发，保护器保留** |
| 字段丢失率 | >1% | 0% | 漏条目重补重试 1 次 → **未触发，防御性保留** |

## 实测结果（2026-08-28，DeepSeek v4-flash）

> 配置：base_url `https://api.deepseek.com`、模型 `deepseek-v4-flash`、batch=20、59 条样本（48 条含占位符）、每模式 3 请求。
> ⚠️ `deepseek-chat`/`deepseek-reasoner` 已于 **2026-07-24 停用**（context7 2026-08 官方文档），必须用 v4 模型名；DeepSeek 不支持 `json_schema strict`（400），只支持 `json_object`。

| 指标 | none（默认） | json_object | 判定 |
| :--- | :--- | :--- | :--- |
| 非法JSON率 | **33.33%**（1/3 批，模型返回**空 content**） | **0.00%** | response_format 必须开启 → 触发 |
| 占位符破坏率 | 0.00% | 0.00% | 保护器工作正常，未触发强制重试 |
| 字段丢失率 | 0.00% | 0.00% | 未触发，重试策略防御性保留 |
| 平均耗时 | 12.5s/批 | 7.9s/批 | 串行吞吐 ~2.5 条/s（含首包延迟），10 万条串行约 11h |

失败样本存档：`tests/spikes/spike3_ai/report_default.json` / `report_jsonobject.json`（gitignored）。
样本量说明：59 条点估计；M3 用真实抽取量回归校验。默认模式的"空 content"是真实失败（模型裸跑时偶尔不产出），不是解析 bug。

## 实测步骤（已执行）

```bash
cd tests/spikes/spike3_ai
# .env 填 OPENAI_API_KEY（支持 OPENAI_* / DEEPSEEK_API_KEY），脚本自动加载
python run_batch.py --limit 59 --out report_default.json        # 默认（none）
python run_batch.py --limit 59 --response-format json_object --out report_jsonobject.json
# json_schema 仅限支持 strict schema 的端点（OpenAI 官方）；DeepSeek 会 400
```

## 后果（Consequences）

- M3 Provider：OpenAICompatibleProvider 一套代码通吃 OpenAI/DeepSeek/Qwen/豆包，`response_format` 做**能力协商**：优先 json_schema → 不支持（如 DeepSeek 实测 400）则降级 json_object → 再降级普通 JSON；配置项 `response_format: auto|json_schema|json_object|none`，默认 auto
- M3 Validator：占位符集合比对、JSON 合法性、漏译检测（返回原文即判失败），重试 1 次仍失败 → 标 warning + 保留 AI 结果供人工审
- 提示词评测集（M3 3.4）把占位符保留率 100% 作为回归门槛
