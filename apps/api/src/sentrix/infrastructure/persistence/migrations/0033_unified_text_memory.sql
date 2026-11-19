ALTER TABLE observations ADD COLUMN source_metadata_json TEXT NOT NULL DEFAULT '{}';
UPDATE observations SET source_metadata_json='{"source_type":"email"}',source_type='text',event_type='text_record'
    WHERE source_type='email';
UPDATE events SET source_type='text',event_type='text_record',
    source_metadata_json=json_set(source_metadata_json,'$.source_type','email') WHERE source_type='email';
DROP INDEX idx_email_external_id;
CREATE UNIQUE INDEX idx_text_external_id ON observations(scope_id,source_type,external_id)
    WHERE source_type='text' AND external_id!='';
ALTER TABLE email_import_state RENAME TO text_import_state;
CREATE VIEW email_import_state AS SELECT * FROM text_import_state;
UPDATE memory_graph_edges SET relation='text_refers_to_event' WHERE relation='email_refers_to_event';
UPDATE memory_graph_edges SET relation='text_time_candidate' WHERE relation='email_time_candidate';
