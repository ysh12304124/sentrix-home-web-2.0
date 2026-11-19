"""Run a fixed prefix of registered email QA through the real Assistant HTTP workflow.

QA/GT are used only for scoring this report, never uploaded as memory or model context.
"""

import argparse
import json
import time
from pathlib import Path

import requests


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--api", required=True)
    p.add_argument("--scope", required=True)
    p.add_argument("--qa", required=True, type=Path)
    p.add_argument("--output", required=True, type=Path)
    p.add_argument("--limit", type=int, default=10)
    p.add_argument("--qa-id", action="append", default=[])
    args = p.parse_args()
    base = args.api.rstrip("/") + "/api/v1"
    rows = [json.loads(line) for line in args.qa.read_text().splitlines() if line.strip()]
    if args.qa_id:
        rows = [row for row in rows if row["qa_id"] in args.qa_id]
        if {row["qa_id"] for row in rows} != set(args.qa_id):
            raise ValueError("QA ID not found")
    rows = rows[: args.limit]
    results = []
    for row in rows:
        start = time.monotonic()
        response = requests.post(
            base + "/assistant/conversations",
            json={"scope_id": args.scope, "title": "Email acceptance " + row["qa_id"]},
            timeout=30,
        )
        response.raise_for_status()
        cid = response.json()["id"]
        response = requests.post(
            base + f"/assistant/conversations/{cid}/turns",
            json={"message": row["question"], "include_debug": True},
            timeout=30,
        )
        response.raise_for_status()
        tid = response.json()["turn_id"]
        while True:
            try:
                response = requests.get(base + "/assistant/turns/" + tid, timeout=120)
            except requests.ReadTimeout:
                if time.monotonic() - start > 300:
                    raise TimeoutError(tid) from None
                continue
            response.raise_for_status()
            turn = response.json()
            if turn["status"] not in {"pending", "running"}:
                break
            if time.monotonic() - start > 300:
                raise TimeoutError(tid)
            time.sleep(2)
        result = {
            "qa_id": row["qa_id"],
            "question": row["question"],
            "expected": row["answer"],
            "expected_refs": row.get("retrieval_media_refs", []),
            "turn_id": tid,
            "seconds": round(time.monotonic() - start, 2),
            "turn": turn,
        }
        results.append(result)
        args.output.write_text(json.dumps(results, ensure_ascii=False, indent=2))
        print(
            json.dumps(
                {
                    "done": len(results),
                    "status": turn["status"],
                    "seconds": result["seconds"],
                    "qa_id": row["qa_id"],
                }
            ),
            flush=True,
        )


if __name__ == "__main__":
    main()
