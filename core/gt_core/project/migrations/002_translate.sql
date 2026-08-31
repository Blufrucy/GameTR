-- M3 翻译任务与用量表（路线图 3.3 断点续翻 + 3.2 用量统计）。
-- 纪律：已发布迁移不可修改；本文件随 schema_version 1→2 一次性应用。

-- 翻译任务态（不进项目状态机——项目 translating⇄reviewing 是粗粒度，
-- 任务 running/paused/cancelled/done/error 表达单次翻译的细粒度生命周期）
CREATE TABLE translate_tasks(
  task_id TEXT PRIMARY KEY,
  provider_id TEXT NOT NULL,
  model TEXT NOT NULL,
  style_id TEXT,
  glossary_version TEXT,            -- 任务启动时的术语表内容哈希（参与 cache_key）
  status TEXT NOT NULL DEFAULT 'running',  -- running/paused/cancelled/done/error
  total INTEGER NOT NULL DEFAULT 0,
  done INTEGER NOT NULL DEFAULT 0,
  error TEXT,
  created_at REAL NOT NULL,
  updated_at REAL NOT NULL
);

-- 断点续翻：批划分落表（task_id+batch_no 唯一）。kill -9 后在途批回滚，
-- 重启按 batch 状态跳过已完成批——分组算法改动的风险（few-shot 上下文不一致）
-- 由「批次契约测试」锁定（改分组=改批次契约）。
CREATE TABLE translate_task_batches(
  task_id TEXT NOT NULL,
  batch_no INTEGER NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending',  -- pending/done
  entry_ids_json TEXT NOT NULL,           -- 该批条目 id 列表
  PRIMARY KEY (task_id, batch_no)
);

-- 用量统计（translate.stats / M4 成本面板）
CREATE TABLE translate_usage(
  id INTEGER PRIMARY KEY,
  task_id TEXT NOT NULL,
  provider_id TEXT NOT NULL,
  model TEXT NOT NULL,
  tokens_in INTEGER NOT NULL DEFAULT 0,
  tokens_out INTEGER NOT NULL DEFAULT 0,
  estimated_cost REAL NOT NULL DEFAULT 0,
  created_at REAL NOT NULL
);
CREATE INDEX idx_translate_usage_task ON translate_usage(task_id);
