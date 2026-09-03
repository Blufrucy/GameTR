-- M4 机翻基线：人工改译文后保留「AI 原始输出」，供机翻·已改条目查看/恢复。
-- translation = 当前生效译文（机翻或人工改后）；machine_text = 最近一次机翻输出（基线）。
-- 人工编辑（entries.update 改 translation/edited）不碰 machine_text；AI 落库时基线=本次输出。
-- 语义联动：纯机翻未改（edited=0）时两者相同；从未机翻过（人工直填）为 NULL。

ALTER TABLE entries ADD COLUMN machine_text TEXT;

-- 历史纯机翻条目（未被人工动过）基线回填=现有译文；已改过的历史条目 AI 原文已丢，无法恢复
UPDATE entries SET machine_text = translation
WHERE machine_text IS NULL AND status = 2 AND edited = 0;
