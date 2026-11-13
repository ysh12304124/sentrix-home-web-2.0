-- 运行中作业的取消请求；执行器在安全检查点读取后停止后续阶段。
ALTER TABLE import_jobs ADD COLUMN cancel_requested INTEGER NOT NULL DEFAULT 0;
