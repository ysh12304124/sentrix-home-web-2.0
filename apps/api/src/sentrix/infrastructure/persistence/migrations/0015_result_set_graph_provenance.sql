-- Freeze the graph path explanation together with the result set.
--
-- A result set is a retrieval snapshot. Without this column, get_result_page would
-- lose the graph relation after the first page (or after an API restart), making the
-- model see a different explanation for the same candidate.
ALTER TABLE agent_result_sets ADD COLUMN graph_provenance_json TEXT NOT NULL DEFAULT '{}';
