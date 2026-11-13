-- Result-set persistence already writes this field; the schema must accept it.
ALTER TABLE agent_result_sets ADD COLUMN graph_provenance_json TEXT NOT NULL DEFAULT '{}';
