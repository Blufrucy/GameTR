# ADR-0003：为何 locators 不透明

- 状态：已接受
- 日期：2026-08-27
- 相关：M1 的 IR 数据模型（Entry.locator）、M2 的 RPGMV 插件（JSON Pointer）

## 背景（Context）

每个引擎的文本定位方式不同：RPGMV 用 JSON Pointer（`$.items[3].note`），Ren'Py 将来可能是 `.rpy` 行号/语句索引。这些定位信息必须随条目存储，写回(WriteBack)时才能把译文放回原位置。

核心（gt-core）是引擎无关的。如果核心"理解"locator 的含义，每个新引擎都要改核心 schema，违反协议纪律"IR 字段只加不改不删"。

## 决策（Decision）

**locator 对核心是不透明字符串**：插件负责生成与解析，核心只负责**存与序列化**，永不解析其含义。

- 核心提供的契约：`Entry.locator` 是字符串，必须随条目持久化、可序列化
- 插件提供的契约：detect/extract 返回的条目必须带 locator；locator 的语义只对生成它的插件成立
- 测试责任：核心侧做 locator 序列化契约测试（只存不解析）；插件侧做 locator 有效性契约测试

## 备选方案与拒绝理由

| 方案 | 拒绝理由 |
| :--- | :--- |
| 核心定义结构化 locator（{type, path, pointer}） | 核心被迫演化通用定位模型，引擎新格式都来改核心 schema，破坏协议稳定性 |
| 核心完全不存 locator | 无法实现写回，不可行 |
| 不透明字符串（采纳） | 格式升级由插件版本管理，核心 API 不变；协议稳定 |

## 后果（Consequences）

- locator 的格式约定记录在插件文档（RPGMV = JSON Pointer 风格 `$.a[0].b`）
- 未来 Ren'Py 插件引入新格式，核心零改动
- M1 存储层把 locators 序列化进 `locators_json` 列，不解析
