# ADR-0003：为何 locators 不透明 + 写回锚点（字节区间）

- 状态：已接受（2026-08-28 补充「写回锚点」）
- 日期：2026-08-27（初稿）、2026-08-28（补充字节区间契约）
- 相关：M1 的 IR 数据模型（Entry.locator）、M2 的 RPGMV 插件（JSON Pointer）、ADR-0004（write_back 算法）

## 背景（Context）

每个引擎的文本定位方式不同：RPGMV 用 JSON Pointer（`$.items[3].note`），Ren'Py 将来可能是 `.rpy` 行号/语句索引。这些定位信息必须随条目存储，写回(WriteBack)时才能把译文放回原位置。

核心（gt-core）是引擎无关的。如果核心"理解"locator 的含义，每个新引擎都要改核心 schema，违反协议纪律"IR 字段只加不改不删"。

## 决策（Decision）

**locator 对核心是不透明字符串**：插件负责生成与解析，核心只负责**存与序列化**，永不解析其含义。

- 核心提供的契约：`Entry.locator` 是字符串，必须随条目持久化、可序列化
- 插件提供的契约：detect/extract 返回的条目必须带 locator；locator 的语义只对生成它的插件成立
- 测试责任：核心侧做 locator 序列化契约测试（只存不解析）；插件侧做 locator 有效性契约测试

## 写回锚点（字节区间）

**翻译器的 write_back 用「字节区间替换」，不用「重序列化」**（算法见 ADR-0004，正解由 Spike 2 验证）：

extract 时插件为每个可翻译字符串记录其在原文件里的字符区间 `[start, end)`（含引号），
write_back 时只替换该区间，其余字节原样保留 → 格式无关（CRLF / 缩进 / 插件怪癖天然保留，翻译器对格式免疫）。

**一条 Entry 可以有多个区间**（RPGMV 连续 401 指令拼接成段落，见 ADR-0004 与 `plugins/rpgmv/extract.py`），
故 context_json 里存的是**区间列表** + **每区间原文**：

```json
{"file_path": "Map001.json", "char_ranges": [[870,887],[927,949]],
 "segments": ["こんにちは、\\N[1]勇者！", "次の行も同じメッセージ。"]}
```

- `char_ranges`：写回锚点，逐区间替换
- `segments`：每区间的原文（401 参数可内嵌 `\n`，write_back 按段还原行数，不能靠 `source.split("\n")`——会把段内换行与段间换行混在一起）

**字节区间与 locator 分工**（两者生命周期不同，分开存）：

| | locator | 字节区间 |
| :--- | :--- | :--- |
| 定位维度 | 语义位置（RPGMV 为 `{文件}::{JSON Pointer}`） | 物理位置（字符偏移） |
| 用途 | 给人读、参与稳定 ID、校验/去重 | 给 write_back 机器切片替换 |
| 稳定性 | 相对稳定（游戏小改语义位置可能不变） | 依赖原文不变（原文变即失效，须重新 extract） |
| 存储 | `Entry.locator`（`locators_json` 列） | `Entry.context_json` 的 key（`char_ranges`/`segments`） |

**字节区间存 `context_json`，不进 locator**：context_json 是插件写入的元信息容器
（`file_path` 已在此，见 repo.py 文件过滤），`char_ranges` 同属这类，核心零改动。
locator 参与稳定 ID 计算（entry_id），字节区间不参与（否则字节变 ID 变，但语义没变，语义稳定才是 ID 的诉求）。
RPGMV 的 locator 必须含文件名（`Items.json::$[1].name`）——不同数据库文件的 root 都是数组，
同名 pointer 指向不同文本，不带文件名会 ID 碰撞、落库互相覆盖（真实工程实测发现）。

## 备选方案与拒绝理由

| 方案 | 拒绝理由 |
| :--- | :--- |
| 核心定义结构化 locator（{type, path, pointer}） | 核心被迫演化通用定位模型，引擎新格式都来改核心 schema，破坏协议稳定性 |
| 核心完全不存 locator | 无法实现写回，不可行 |
| 字节区间放进 locator | locator 参与稳定 ID，物理位置随原文变会让 ID 漂移；语义位置与物理位置生命周期不同，混存互相污染 |
| 字节区间作为 Entry 新字段 | 违反"字段尽量只加不改不删"的最小化原则；context_json 已能承载，无需动 schema |
| 不透明字符串（采纳） | 格式升级由插件版本管理，核心 API 不变；协议稳定 |

## 后果（Consequences）

- locator 的格式约定记录在插件文档（RPGMV = `{文件basename}::{JSON Pointer}`，如 `Map001.json::$.events[1].pages[0].list[3].parameters[0]`）
- 未来 Ren'Py 插件引入新格式，核心零改动
- M1 存储层把 locators 序列化进 `locators_json` 列，不解析
- 字节区间随 context_json 持久化（`{"file_path": ..., "char_ranges": [[s,e],...], "segments": [...]}`），核心只存不解析
- **写回前提**：字节区间依赖 extract 时的原文字节不变。extract → write_back 之间源文件不得被手动改
  （翻译器工作流满足：write_back 读原始文件、输出到新目录，不写原目录）
