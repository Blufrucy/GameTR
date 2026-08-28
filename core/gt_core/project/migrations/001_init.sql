-- M1 首版表结构（路线图 1.3）：字段首版定稿，后续加列容易、改名难，故谨慎。
-- 协议纪律：列名与 protocol/schema/common.json 的字段语义对齐。

CREATE TABLE meta(
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

-- schema_version 由迁移器维护，初始为 0；其余 meta（engine_id/source_path/project_state/created_at）由 Project.create 写入
INSERT INTO meta(key, value) VALUES ('schema_version', '0');

CREATE TABLE entries(
  id TEXT PRIMARY KEY,
  source TEXT NOT NULL,
  translation TEXT,
  status INTEGER NOT NULL,          -- 1..4 对应 EntryStatus（见 common.json）
  locators_json TEXT NOT NULL,      -- JSON 数组；首个元素即协议 Entry.locator（ADR-0003：核心只存不解析）
  context_json TEXT,
  warnings_json TEXT,
  updated_at REAL NOT NULL
);
CREATE INDEX idx_entries_status ON entries(status);

-- FTS5 全文索引：external content 指向 entries，由 triggers 自动同步（永不漂移）。
-- tokenize=trigram：中日文无空格分词，unicode61 会把整句当一个 token 导致子串查不到；
-- trigram 支持 >=3 字符子串匹配（2 字符查询在 repo.search 走 LIKE 降级）。
CREATE VIRTUAL TABLE entries_fts USING fts5(
  source, translation,
  content='entries',
  content_rowid='rowid',
  tokenize='trigram'
);

CREATE TRIGGER entries_ai AFTER INSERT ON entries BEGIN
  INSERT INTO entries_fts(rowid, source, translation)
  VALUES (new.rowid, new.source, new.translation);
END;

CREATE TRIGGER entries_ad AFTER DELETE ON entries BEGIN
  INSERT INTO entries_fts(entries_fts, rowid, source, translation)
  VALUES ('delete', old.rowid, old.source, old.translation);
END;

-- WHEN 条件：只同步 source/translation 变化；status/updated_at 等改动
-- 不需要动 FTS（review：status-only 批量更新曾触发每行 delete+reinsert 重索引）
CREATE TRIGGER entries_au AFTER UPDATE ON entries
WHEN new.source IS NOT old.source OR new.translation IS NOT old.translation
BEGIN
  INSERT INTO entries_fts(entries_fts, rowid, source, translation)
  VALUES ('delete', old.rowid, old.source, old.translation);
  INSERT INTO entries_fts(rowid, source, translation)
  VALUES (new.rowid, new.source, new.translation);
END;

CREATE TABLE glossary(
  id INTEGER PRIMARY KEY,
  term TEXT NOT NULL UNIQUE,  -- 按 term 去重（glossary.upsert 语义）
  translation TEXT NOT NULL,
  match_case INTEGER DEFAULT 0
);

CREATE TABLE translate_cache(
  cache_key TEXT PRIMARY KEY,
  source TEXT,
  result TEXT,
  provider_id TEXT,
  model TEXT,
  created_at REAL
);
