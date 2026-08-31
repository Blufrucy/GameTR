# Spike 4：AI 翻译真实性验证（2026-08-28）

**回答**：往返流程跑通 ≠ AI 翻译成功。本 spike 验证**后者**——真实 API + 真实游戏文本 +
占位符保护器 + 写回的完整链路，12 条真实文本全部翻译成功。

## 结论

| 指标 | 结果 |
| :--- | :--- |
| 非法 JSON 率 | **0 / 12**（json_object） |
| 字段丢失率 | **0 / 12** |
| 占位符破坏率 | **0 / 12**（`\N[1]` `\C[2]` `\G` 全保留） |
| 吞吐 | **~1.1–1.6 条/s**（DeepSeek v4-flash，12 条 / ~11s） |
| 译文质量 | 日文→中文准确自然（人工审：见下样例） |
| 写回链路 | **12 条写入、0 warning、再提取一致=True** |

## 样例（日→中，占位符原样保留）

```
こんにちは、\N[1]勇者！ / 次の行も同じメッセージ。\C[2]赤色
→ 你好，\N[1]勇者！ / 下一行也是同样的消息。\C[2]红色

スクロールテキスト\G → 滚动文本\G
勇者アリス → 勇者爱丽丝       魔法使いボブ → 魔法师鲍勃
伝説の勇者1。 → 传说中的勇者1。   村で生まれ育った少女。 → 在村庄长大的少女。
```

## 实测发现（M3 设计约束）

1. **模型会跟随 user 输入的 JSON 结构当模板**（少样本学习）：给数组 `[{id,text}]` 就回数组，
   给 `{"items":[...]}` 就回 `{"items":[{id,text}]}`，字段名照抄。→ M3 prompt 必须
   **在 system 里显式声明响应结构**（如 `{"translations":[{id,translation}]}`），解析器
   仍要做多形态防御兼容。
2. **json_object 必须开**（ADR-0005）：默认模式 33% 非法 JSON（spike3 实测），json_object 0%。
3. **吞吐 1.1 条/s 是单请求串行**；M3 应并发（asyncio + Semaphore），按路线图预估可到 10+ 条/s。

## 用法

```bash
# 配置从 ../spike3_ai/.env 读（OPENAI_API_KEY/BASE_URL/MODEL）
uv run python tests/spikes/spike4_ai_translate/run.py [--max-entries 10] [--batch 10]
```

## 文件

- `run.py` — 完整链路：提取黄金样本 → protect → DeepSeek(json_object) → restore → 校验 → write_back → re-extract
- 依赖 M2 产物：`plugins/rpgmv/protector.py`（占位符保护器）、RPGMV 插件（extract/write_back）

## 未验证（诚实边界）

- 译文**质量**只审了 12 条，未做大规模评测（M3 要建 100 条提示词评测集）
- 未验证游戏内实际显示（需 M4 GUI 或手动启动游戏）
- 未测长文本/特殊符号（如 `\x[1]` 罕见控制符、emoji）
