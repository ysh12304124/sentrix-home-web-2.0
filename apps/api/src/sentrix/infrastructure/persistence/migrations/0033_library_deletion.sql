-- Only requests explicitly submitted by the library are advanced automatically.
ALTER TABLE media_deletions ADD COLUMN library_state TEXT
 CHECK(library_state IN ('pending','submitted','failed','blocked'));
CREATE INDEX media_deletions_library ON media_deletions(library_state, scope_id, created_at);
