-- 后处理是导入的第二段，可能比逐文件建库更久。把当前阶段与事件总结计数
-- 持久化，用户页才能解释“为什么进度条还没走完”，而不是卡在 100%。
ALTER TABLE scope_postprocess ADD COLUMN stage TEXT NOT NULL DEFAULT '';
ALTER TABLE scope_postprocess ADD COLUMN stage_started_at TEXT;
ALTER TABLE scope_postprocess ADD COLUMN events_done INTEGER NOT NULL DEFAULT 0;
ALTER TABLE scope_postprocess ADD COLUMN events_total INTEGER NOT NULL DEFAULT 0;
