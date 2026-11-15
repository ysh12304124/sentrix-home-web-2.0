"""Start the authenticated local Mac album entry without changing existing accounts."""

from __future__ import annotations

import json
import os
import plistlib
import secrets
import subprocess
import sys
import time
from pathlib import Path


def main() -> None:
    root = Path(__file__).resolve().parents[2]
    if sys.platform != "darwin":
        raise SystemExit("This launcher requires macOS launchd")
    directory = root / "data" / "local-control"
    directory.mkdir(parents=True, exist_ok=True)
    directory.chmod(0o700)
    env = {
        "SENTRIX_CONTROL_DB_PATH": str(directory / "control.db"),
        "SENTRIX_CONTROL_USERS_FILE": str(directory / "users.json"),
        "SENTRIX_CONTROL_MODEL_CLIENTS_FILE": str(directory / "model-clients.json"),
        "SENTRIX_CONTROL_FEEDBACK_DIR": str(directory / "feedback"),
        "SENTRIX_CONTROL_SECURE_COOKIE": "false",  # Bound exclusively to loopback HTTP.
        "SENTRIX_CONTROL_MODEL_UPSTREAM_URL": os.getenv("SENTRIX_VLM_BASE_URL", "http://127.0.0.1:18100/v1"),
        "SENTRIX_CONTROL_UNMANAGED_MODEL_CONSUMERS": "local-album,photobench",
        "PYTHONPATH": f"{root}/apps/api/src:{root}/apps/photobench/src",
        "NO_PROXY": "127.0.0.1,localhost",
        "no_proxy": "127.0.0.1,localhost",
    }
    from sentrix.control.store import CONTROL_MIGRATIONS_DIR, ControlStore
    from sentrix.infrastructure.persistence.database import Database

    store = ControlStore(Database(directory / "control.db", CONTROL_MIGRATIONS_DIR))
    store.migrate()
    users_path = directory / "users.json"
    routes = json.loads(users_path.read_text()) if users_path.exists() else {"users": {}}
    accounts = [
        ("album", "我的相册", "http://127.0.0.1:8191", os.getenv("SENTRIX_DATA_DIR", str(root / "data"))),
        (
            "benchmark",
            "487 测试相册",
            os.getenv("PHOTOBENCH_SENTRIX_API_URL", "http://127.0.0.1:18223"),
            os.getenv("PHOTOBENCH_SENTRIX_DATA_DIR", str(root / "data/photobench-sentrix")),
        ),
    ]
    credentials = directory / "新建账号登录信息.txt"
    for account, name, url, data in accounts:
        with store.database.connect() as conn:
            exists = conn.execute("SELECT id FROM accounts WHERE id=?", (account,)).fetchone()
        if not exists:
            password = secrets.token_urlsafe(16)
            store.create_account(
                account, display_name=name, role="user", password=password, api_url=url, data_root=data
            )
            with credentials.open("a", encoding="utf-8") as out:
                out.write(f"账号：{account}\n密码：{password}\n入口：http://127.0.0.1:8210/\n\n")
            credentials.chmod(0o600)
        route = routes["users"].setdefault(account, {"api_token": secrets.token_urlsafe(32)})
        route["api_url"] = url
    users_path.write_text(json.dumps(routes), encoding="utf-8")
    users_path.chmod(0o600)
    clients = directory / "model-clients.json"
    if not clients.exists():
        clients.write_text('{"clients":{}}')
        clients.chmod(0o600)
    label = "local.sentrix.album-control"
    service = {
        "Label": label,
        "ProgramArguments": [
            sys.executable,
            "-m",
            "uvicorn",
            "--factory",
            "sentrix.control.main:create_app",
            "--host",
            "127.0.0.1",
            "--port",
            "8210",
            "--app-dir",
            str(root / "apps/api/src"),
        ],
        "WorkingDirectory": str(root),
        "EnvironmentVariables": env,
        "KeepAlive": True,
        "RunAtLoad": True,
        "StandardOutPath": str(directory / "service.log"),
        "StandardErrorPath": str(directory / "service-error.log"),
    }
    plist = directory / f"{label}.plist"
    plist.write_bytes(plistlib.dumps(service))
    plist.chmod(0o600)
    domain = f"gui/{os.getuid()}"
    exists = subprocess.run(["launchctl", "print", f"{domain}/{label}"], capture_output=True).returncode == 0
    if exists:
        subprocess.run(["launchctl", "bootout", f"{domain}/{label}"], check=True)
    # launchd can return from bootout before the label is fully deregistered.
    for attempt in range(10):
        result = subprocess.run(["launchctl", "bootstrap", domain, str(plist)], capture_output=True)
        if result.returncode == 0:
            break
        if attempt == 9:
            raise RuntimeError(result.stderr.decode(errors="replace"))
        time.sleep(0.5)
    print(f"Authenticated album: http://127.0.0.1:8210/; credentials: {directory}")


if __name__ == "__main__":
    main()
