# Spike 3：AI 结构化输出可靠性测试（路线图 1.5）

**目标**：统计目标模型返回结构化 JSON 的可靠性，决定是否必须 `response_format` + 重试策略。
结论（含实测数据与判定）写入 **ADR-0005**。

## 指标

| 指标 | 含义 | 判定阈值（预设，实测后按 ADR-0005 定稿） |
| :--- | :--- | :--- |
| 非法JSON率 | 解析失败 / 总请求数 | >2% → 必须开 response_format |
| 占位符破坏率 | 译文占位符序列不一致 / 有译文条目数 | >1% → 流水线必须带保护器+还原器 |
| 字段丢失率 | 缺 id 或缺 translation / 收到条目数 | >1% → 需要重试/校验 |
| 平均耗时 | 单请求往返 | 用于估算批量翻译吞吐 |

## 快速开始

```bash
export OPENAI_API_KEY=sk-...
# 或任意 OpenAI 兼容端点
export OPENAI_BASE_URL=https://api.deepseek.com/v1
export OPENAI_MODEL=deepseek-chat

python run_batch.py --limit 100                          # 默认（不请求结构化）
python run_batch.py --limit 100 --response-format json_schema
python run_batch.py --limit 100 --response-format json_object
python run_batch.py --dry-run                            # 无密钥自检
```

报告落到 `report.json`（含全部失败样本，便于人工审阅）。

## 占位符保护如何工作

发请求前把 RPGMV 占位符（`\C[n] \I[n] \N[n] \V[n] \G \{ \. \^ \| \!`）替换为
`⟦0⟧⟦1⟧…`，收到译文后比对 `⟦n⟧` 的**数量+顺序+编号**是否一致——这是占位符破坏率。

## 结果如何进 ADR-0005

跑完后把 `report.json` 的 metrics 填进 ADR-0005 的实测表，并据此定稿：

- 非法JSON率超阈值 → M3 的 OpenAICompatibleProvider 默认开 `response_format: json_schema`
- 占位符破坏率超阈值 → 确认 M3 流水线 Protector/Restorer 阶段是硬要求
- 字段丢失率超阈值 → Validator 重试 1 次 + 标 warning 保留 AI 结果的策略
