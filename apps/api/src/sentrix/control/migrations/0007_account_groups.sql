-- 管理台分组。没有记录的用户默认归「项目组」；只有需要单独成组的用户才落一行。
CREATE TABLE IF NOT EXISTS account_groups (
    account_id TEXT PRIMARY KEY REFERENCES accounts(id),
    group_name TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
