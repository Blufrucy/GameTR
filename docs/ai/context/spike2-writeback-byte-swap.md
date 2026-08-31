# Spike 2 架构决策：write_back 从重序列化改为字节区间替换

日期：2026-08-28

## 背景

Spike 2 用真实 MZ 工程（oriontest）验证了 `serialize_rpgm` 重序列化 17/17 复刻。用户又提供了真实 MV
工程（False Awakening Episode 1），验证时发现三种重序列化必须额外处理的格式差异：

1. 空 events 数组展开为 `[\n]`，但同文件 `encounterList:[]` 内联
2. Map001.json 是 CRLF，其余文件是 LF
3. 第三方插件 Doodads.json 整文件 `indent=2`

于是引出一个核心架构问题：**游戏文件格式不是固定规则（MV≠MZ、编辑器≠插件、LF≠CRLF），翻译器怎么统一处理？**

## 结论

**翻译器不需要"统一格式"，只需要"对格式免疫"。**

翻译器的本质操作是"字符串值替换"（原文→译文，从不改结构），所以 write_back 正确做法是**字节区间替换**，
不是整文件重序列化：

- extract 时用带字节区间的解析器 `locate_strings` 记录每个字符串字面量的 `[start, end)`（字符偏移、含引号）
- write_back 时 `apply_text_swap` 只替换那段字节，其余字节原样保留
- 格式（CRLF/缩进/插件怪癖）天然保留，因为根本没碰它

**实测验证**：真实 MV(83)+MZ(17) 共 17580 个字符串，字节区间定位 100% 精确、替换后只有目标值变、
其余字节逐字不变。

## 关键 tradeoff

| | 重序列化（原方案） | 字节区间替换（新方案） |
| :--- | :--- | :--- |
| 要求 | 100% 复刻每引擎每版本 serializer | 只需解析器给出字符串字节区间 |
| 格式怪癖 | 逐个探测/复刻（不可穷尽） | 不碰，天然免疫 |
| 覆盖场景 | 可改结构（翻译不需要） | 只覆盖文本值替换（翻译核心场景） |
| 前提 | 无 | 原文在 extract→write_back 间不变（工作流满足） |

## 核心层零改动

这是关键：**现有架构已正确，问题在 M2 插件的 write_back 实现策略，不在核心**。

- ADR-0003 的不透明 locator 已对：locator = 语义位置（JSON Pointer，给人读、参与稳定 ID）
- Entry 已有 `context_json` 字段（已在存 `file_path`），字节区间 `byte_range` 加进去即可
- 字节区间不进 locator：locator 参与稳定 ID 计算，物理位置随原文变会让 ID 漂移
- 字节区间不进新字段：context_json 已能承载，最小化 schema 变更

## 落地动作（已完成）

- roundtrip.py：`locate_strings` + `apply_text_swap` 正式化，`--edit` 改走字节替换
- ADR-0003 补「写回锚点（字节区间）」节
- ADR-0004 重写：write_back 正解 = 字节区间替换，`serialize_rpgm` 降级黄金样本校验器
- spike2 README、AGENTS.md 同步

## 对 M2 的遗留约束

1. RPGMV 插件 extract 用 `locate_strings`，字节区间写入 `context_json.byte_range`
2. RPGMV 插件 write_back 用 `apply_text_swap`（字节替换），不重序列化
3. `serialize_rpgm` 保留作黄金样本"空翻译 diff=0"校验器
4. extract 必须保留原文字节（字节区间依赖它）
