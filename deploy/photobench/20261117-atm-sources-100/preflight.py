"""Read-only service, source and history inventory. Never emits configuration values."""

from pathlib import Path
import json, hashlib, subprocess, sqlite3, urllib.request, sys

ROOT = Path("/home/realmagic/xy/releases/psh-b2030e1-100-20261117")
SERVICES = {
    8191: ("sentrix2-api.service", str(ROOT) + "-8191", "8191"),
    8291: ("sentrix-atm100-api.service", str(ROOT) + "-8291", "8291"),
    8294: ("sentrix-atm100-doubao-api.service", str(ROOT), "8294"),
    8772: ("sentrix2-photobench.service", str(ROOT) + "-8772", "photobench"),
    8872: ("sentrix-atm100-photobench.service", str(ROOT), "photobench"),
    8874: ("sentrix-atm100-doubao-photobench.service", str(ROOT), "photobench"),
}


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else None


def get(port, path):
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/v1/{path}", timeout=20) as r:
        return json.load(r)


def inspect(manifest):
    out = {"services": {}, "protected": {}}
    for port, (unit, root, variant) in SERVICES.items():
        root = Path(root)
        pid = int(
            subprocess.check_output(["systemctl", "--user", "show", unit, "-p", "MainPID", "--value"]).strip()
        )
        env = dict(
            x.decode().split("=", 1)
            for x in Path(f"/proc/{pid}/environ").read_bytes().split(b"\0")
            if b"=" in x
        )
        keys = [
            "SENTRIX_DATA_DIR",
            "PHOTOBENCH_CONFIG_ROOT",
            "PHOTOBENCH_RESULTS_ROOT",
            "PHOTOBENCH_WEB_DIST_ROOT",
            "PHOTOBENCH_BUILD_REVISION",
            "SENTRIX_WEB_DIST_DIR",
            "PHOTOBENCH_DATA_ROOT",
            "SENTRIX_EMBEDDING_SCHEME",
            "SENTRIX_TEXT_EMBEDDER_URL",
            "SENTRIX_WEMM_URL",
        ]
        item = {
            "unit": unit,
            "root": str(root),
            "pid": pid,
            "env": {k: env[k] for k in keys if k in env},
            "source": {p: sha(root / p) for p in manifest["variants"][variant]["files"]},
            "build_info": json.loads((root / "build-info.json").read_text()),
        }
        if port < 8700:
            datadir = Path(env["SENTRIX_DATA_DIR"])
            item["dist_root"] = env.get("SENTRIX_WEB_DIST_DIR") or str(root / "apps/web/dist")
            dist = Path(item["dist_root"])
            item["dist_files"] = {str(p.relative_to(dist)): sha(p) for p in dist.rglob("*") if p.is_file()}
            item["databases"] = {}
            for db in sorted(
                set(datadir.glob("*.db")) | set(datadir.glob("*.sqlite3")) | set(datadir.glob("*.sqlite"))
            ):
                c = sqlite3.connect(db.as_uri() + "?mode=ro", uri=True)
                tables = {r[0] for r in c.execute("select name from sqlite_master where type='table'")}
                stats = {}
                if "agent_turns" in tables:
                    stats["table_counts"] = {
                        t: c.execute("select count(*) from " + t).fetchone()[0]
                        for t in ["assets", "observations", "memory_spaces", "agent_messages", "agent_turns"]
                        if t in tables
                    }
                    stats["active_turns"] = [
                        list(r)
                        for r in c.execute(
                            "select id,status,created_at from agent_turns where status in ('pending','running') order by id"
                        )
                    ]
                for t in ["import_jobs", "video_jobs"]:
                    if t in tables:
                        stats[t + "_statuses"] = [
                            list(r)
                            for r in c.execute("select status,count(*) from " + t + " group by status")
                        ]
                if "agent_turns" in tables:
                    stats["pending_sha256"] = hashlib.sha256(
                        json.dumps(
                            [
                                list(r)
                                for r in c.execute(
                                    "select * from agent_turns where status='pending' order by id"
                                )
                            ],
                            ensure_ascii=False,
                            default=str,
                        ).encode()
                    ).hexdigest()
                item["databases"][str(db)] = stats
                c.close()
        else:
            item["active_work"] = get(port, "active-work")
            cfg = Path(env["PHOTOBENCH_CONFIG_ROOT"])
            results = Path(env["PHOTOBENCH_RESULTS_ROOT"])
            item["result_inventory"] = {
                str(p): [p.stat().st_size, p.stat().st_mtime_ns]
                for p in results.rglob("*")
                if p.is_file() and p.name != ".photobench-instance.lock"
            }
            for p in list(cfg.rglob("*")) + list(Path(str(results) + "-chat").glob("*.json")):
                if p.is_file():
                    out["protected"][str(p)] = sha(p)
            connection = json.loads((cfg / "runtime_connection.json").read_text())
            item["configured_product_revision"] = connection.get("product_build_revision")
            dist = Path(env.get("PHOTOBENCH_WEB_DIST_ROOT") or root / "apps/photobench/web/dist")
            item["dist_root"] = str(dist)
            item["dist_files"] = {str(p.relative_to(dist)): sha(p) for p in dist.rglob("*") if p.is_file()}
        out["services"][str(port)] = item
    return out


if __name__ == "__main__":
    print(json.dumps(inspect(json.loads(Path(sys.argv[1]).read_text())), indent=2))
