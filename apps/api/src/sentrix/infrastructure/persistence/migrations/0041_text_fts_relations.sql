CREATE VIRTUAL TABLE text_source_fts USING fts5(observation_id UNINDEXED, title, body, tokenize='unicode61');
CREATE TABLE text_source_projection (
 observation_id TEXT PRIMARY KEY REFERENCES observations(id) ON DELETE CASCADE,
 revision INTEGER NOT NULL
);
CREATE TABLE text_source_link_keys (
 observation_id TEXT NOT NULL REFERENCES text_source_projection(observation_id) ON DELETE CASCADE,
 kind TEXT NOT NULL,
 link_key TEXT NOT NULL,
 PRIMARY KEY(observation_id,kind,link_key)
);
CREATE INDEX text_source_link_lookup ON text_source_link_keys(kind,link_key);
