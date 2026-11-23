"""Read menu evidence paths and observation text for the disputed strawberry dessert."""

import json
import sqlite3

from inspect_terminal import DB, current, save

reader, state, _ = current()
qa_id = "atm-c2d9d893-99d1-4482-99dc-085b074ea099"
qa = next(r for r in state["qa_snapshot"]["rows"] if r["qa_id"] == qa_id)
assets = []
with sqlite3.connect("file:" + str(DB) + "?mode=ro", uri=True) as db:
    db.row_factory = sqlite3.Row
    for ref in qa["answer_evidence_media_refs"]:
        for row in db.execute(
            "select id,file_name,path,captured_at,content_sha256 from assets "
            "where scope_id=? and file_name=?",
            (state["scope_id"], ref["media_id"]),
        ):
            item = dict(row)
            item["observations"] = [
                dict(r) for r in db.execute("select * from observations where asset_id=?", (item["id"],))
            ]
            assets.append(item)
result = {"qa_id": qa_id, "qa": qa, "read_revision": reader.revision, "assets": assets, "read_only": True}
save("menu-evidence.json", result)
print(json.dumps(result, ensure_ascii=False), flush=True)
