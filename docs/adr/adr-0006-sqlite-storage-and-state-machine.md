# ADR-0006：SQLite 存储层与状态机设计（M1）

- 状态：已实施（M1，2026-08-28）
- 来源：路线图 1.3/1.4
- 相关：ADR-0002（NDJSON/UTF-8）、ADR-0003（locator 不透明）

## 背景（Context）

M1 要给核心建持久化存储（项目文件）+ 状态机 + 全量 RPC。关键取舍点：连接模型、迁移策略、FTS 中文搜索、单条与批量状态语义。

## 决策（Decision）

### 1. 连接模型：单连接 + WAL，不做连接池

sidecar 进程一次服务一个项目（GUI 一次开一个工程）。单连接 + 事务纪律最可控；
WAL 允许 GUI 读与流水线写并发不互相阻塞。`ConnectionManager` 按路径持有单条连接。

### 2. 迁移机制：目录按序号连续，meta 记录版本，空库视为 v0

`migrations/NNN_*.sql` 按序执行，`meta.schema_version` 记录当前版本。
**空库（meta 表不存在）视为版本 0**，让 001_init.sql 负责建表——解决"查版本时表还不存在"的鸡生蛋问题。
版本必须连续（跳跃即 MigrationError）。老项目打开时自动逐级升级（向后兼容从这里开始）。
纪律：**已发布的迁移文件不可修改**，新改动必须加新迁移（M1 首版定稿前的修正允许改 001）。

### 3. 事务粒度（路线图纪律）

- 批量插入/批量改状态：整批一个事务
- 单条更新：单条一个事务
- 项目元数据（meta 写入）：必须显式 commit（实测坑：executemany 不带事务在
  Python sqlite3 默认不落盘，close 后 reopen 读不到）

### 4. FTS 中文搜索：trigram tokenizer + 短查询 LIKE 降级

unicode61 对中日文不做子串匹配（"你好勇者" 是整 token，查"勇者"不命中）。
改用 **trigram**（>=3 字符子串匹配）。trigram 的 MATCH 是 AND 语义（同一行须含
全部 3-gram），因此查询词必须真实存在于某行。**<3 字符查询 trigram 无法匹配**，
降级 `LIKE '%term%'`（通配符 %/_ 转义 + ESCAPE），5 万条 <100ms。
FTS 表用 external content 指向 entries，triggers（AFTER INSERT/DELETE/UPDATE）
自动同步，杜绝索引漂移。

### 5. locators_json（数组）与 Entry.locator（单数）的桥接

协议 Entry.locator 是单数不透明字符串；表结构定稿用 `locators_json`（数组）。
M1 桥接：locators_json 存单元素数组，首个元素即 Entry.locator（只存不解析，ADR-0003）。
M2 若需要同文本多位置合并，走协议"只加"演进（加 locators 字段 + 迁移）。

### 6. 状态机：单条守卫 vs 批量保护语义分离

- **单条**（entries.update）：严格状态机 `PENDING→MACHINE→EDITED→CONFIRMED`，
  非法迁移抛 InvalidStateTransition（RPC 映射 PROJECT_ERROR）。逐级前进，不可跳级/回退。
- **批量**（batch_update_status）：重翻工具，允许任意目标状态，但
  **CONFIRMED 条目一律跳过**（路线图：一键重翻不得覆盖 CONFIRMED）。
- 项目状态机 `created→detecting→extracted→translating⇄reviewing→writing_back→done`，
  非法迁移抛异常（守卫防插件乱改）。

### 7. 参数校验：pydantic + extra=forbid

MethodRegistry.register 支持 schema 参数，进方法前 `model_validate`，
失败返回 INVALID_PARAMS。`extra=forbid`：未知字段直接拒绝，防契约漂移。
校验通过后 `model_dump(exclude_unset=True)` 转回 dict——未传字段不进 handler，
默认值由 handler 决定（不注入隐式默认，保持 JSON-RPC "未提供"语义）。

### 8. 错误码扩充（只加不改）

-32002 NO_PROJECT（未打开项目）、-32003 PROJECT_ERROR（文件/迁移/状态守卫失败）。

## 后果（Consequences）

- 5 万条数据：插入 <5s、分页 <50ms、FTS/LIKE 搜索 <100ms（test_perf 覆盖）
- 10 万条全流程验收：`python tests/e2e/m1_flow.py`（tests/e2e/m1_flow.py）
- RPC 日志回放：`logs/rpc-*.ndjson` 逐行重放不崩溃（m1_flow 阶段 3 验证）
- 核心模块覆盖率 93%（验收要求 >80%）
