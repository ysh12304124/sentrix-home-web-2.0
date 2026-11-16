-- assets 表添加 error 列，用于记录导入/处理失败的错误信息。
-- 配合 update_status(asset_id, status, error) 使用。
ALTER TABLE assets ADD COLUMN error TEXT;
