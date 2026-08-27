# ADR-0005：AI 结构化输出可靠性策略

- 状态：设计中（测试工具已就绪，实测待 API Key）
- 日期：2026-08-27
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

**待实测数据定稿的决策**（判定阈值，路线图 1.5 预设）：

| 指标 | 阈值 | 触发动作 |
| :--- | :--- | :--- |
| 非法JSON率 | >2% | response_format 必须开启（不可降级） |
| 占位符破坏率 | >1% | Validator 对破坏条目强制重试 1 次后标 warning 保留 AI 结果 |
| 字段丢失率 | >1% | 漏条目按缺失重补，重试 1 次 |

## 实测步骤（待执行）

```bash
cd tests/spikes/spike3_ai
export OPENAI_API_KEY=... OPENAI_MODEL=...
python run_batch.py --limit 100
python run_batch.py --limit 100 --response-format json_schema
```

报告 `report.json` 含全部失败样本；把 metrics 填回本 ADR 并定稿判定阈值。

## 后果（Consequences）

- M3 Provider：OpenAICompatibleProvider 一套代码通吃 OpenAI/DeepSeek/Qwen/豆包，`response_format` 作为可配置项，默认 json_schema
- M3 Validator：占位符集合比对、JSON 合法性、漏译检测（返回原文即判失败），重试 1 次仍失败 → 标 warning + 保留 AI 结果供人工审
- 提示词评测集（M3 3.4）把占位符保留率 100% 作为回归门槛
