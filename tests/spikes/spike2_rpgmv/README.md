# Spike 2：RPG Maker 数据文件往返保真（路线图 1.4）

**目标**：验证读 → 改 → 写回的字节级保真，找出能 100% 还原原格式的写回算法。
结论（含"为什么是这个格式"的分析）已写入 **ADR-0004**。

## 已验证的结论（2026-08-28，真实 MZ + MV 双工程）

两个独立能力，各有分工：

### 1. 字节区间替换 = write_back 正解（格式免疫）

翻译器只改字符串值、不改结构，所以 write_back 正确做法是**只替换被翻译字符串的字面量字节区间**，
其余字节原样保留 → 格式无关。对真实工程 **17580 个字符串**逐一验证：定位精确、替换后只有目标值变、
其余字节逐字不变。

```python
from roundtrip import locate_strings, apply_text_swap
loc = locate_strings(原文)              # {path: (值, 字面量start, 字面量end)}
写回 = apply_text_swap(原文, '$.events[1].name', '新文本')  # 只换那段字节
```

### 2. 重序列化 = 黄金样本校验器（不用于写回）

`serialize_rpgm(data, detect_style(原文))` 复刻引擎 serializer，用于校验「不改任何文本时重写 == 原文」。
真实格式（推翻合成样本 `indent=2` 假设）：JS `JSON.stringify` 紧凑 + 含 null 数组逐元素展开。

| 工程 | 版本 | 文件 | 重序列化复刻 | 字节替换 |
| :--- | :--- | :--- | :--- | :--- |
| oriontest | MZ 部署版（rmmz 1.9.0） | 17 | 17/17 | 17580 字符串全对（两工程合计） |
| False Awakening Episode 1 | MV（NW.js） | 83 | 83/83 | 修改写回 98 文件 0 差异* |

\* 2 个文件（DataEX.json/Notes.json）是纯 null 值、无字符串，跳过修改测试。

**MV 工程逼出的三种格式差异**（正是字节替换为何取代重序列化的证据）：
- 空 events 数组展开为 `[\n]`，同文件 `encounterList:[]` 却内联
- Map001 是 CRLF、其余 LF
- 插件 Doodads.json 整文件 `indent=2`

重序列化要探测并复刻这些怪癖（可穷尽性存疑）；字节替换根本不碰它们，天然免疫。

## 待验证

- **加密游戏（.rpgmvp/.rpgmvo）解密后的格式与写回策略**（先解密 → 字节替换 → 再加密）。

## 文件

- `make_sample.py` — 程序化生成最小样本工程（版权干净、可重建，**用真实格式生成**，输出 `sample_game/` 已 gitignore）
- `roundtrip.py` — `locate_strings`+`apply_text_swap`（write_back 核心）+ `serialize_rpgm`（黄金样本校验器）+ 字节 diff 验证

## 用法

```bash
python make_sample.py --out sample_game                    # 生成样本（真实格式）
python roundtrip.py --dir sample_game/www/data             # 空往返校验（重序列化 diff=0）
python roundtrip.py --file <真实游戏>/data/Map001.json \
    --edit '$.events[1].name' '新文本' --diff              # 字节替换改一条并验证
```

## 与 M2 的关系

- locator 用 JSON Pointer 风格（`$.events[1].name`，见 roundtrip.py 解析器）
- M2 黄金样本三类测试中的"空翻译往返 diff=0"就是本 spike 的自动版（`serialize_rpgm` 校验器）
- **M2 write_back 用 `locate_strings`+`apply_text_swap`（字节区间替换）**；extract 时把字节区间写入
  `context_json.byte_range`（见 ADR-0003 写回锚点）
- 字节区间依赖 extract 时的原文字节，故 **extract 必须保留原文**，write_back 读原始文件
