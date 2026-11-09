-- 首次引导的已见版本。跳过与完成都写当前版本；未来内容有实质变化时才提升版本。
ALTER TABLE accounts ADD COLUMN onboarding_version_seen INTEGER NOT NULL DEFAULT 0;
