CREATE TABLE text_memory_chunks (
 observation_id TEXT NOT NULL REFERENCES observations(id) ON DELETE CASCADE,
 chunk_start INTEGER NOT NULL,
 chunk_end INTEGER NOT NULL,
 revision INTEGER NOT NULL,
 model_id TEXT NOT NULL,
 text TEXT NOT NULL,
 vector_json TEXT NOT NULL,
 PRIMARY KEY(observation_id, model_id, chunk_start)
);
