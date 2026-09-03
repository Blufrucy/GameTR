-- M4 人工编辑标记：把「机翻来源」与「人工已改」解耦成两维。
-- 之前 status 单值互斥：机翻(2)→已改(3) 迁移后丢失"来源是机翻"信息，
-- 改过的条目离开机翻筛选（用户反馈：改过的机翻应同时被机翻/已修改命中）。
-- 方案：加 edited 列（1=人工编辑过），机翻条目编辑后 status 保持 2 + edited=1。

ALTER TABLE entries ADD COLUMN edited INTEGER NOT NULL DEFAULT 0;

-- 历史 EDITED(3) 条目 = 人工改过（补 edited 标记，保持"已修改"筛选正确）
UPDATE entries SET edited = 1 WHERE status = 3;
