"""控制服务 app factory 与账号运维命令。

启动时不构造产品 ``Container``：不碰 Qdrant、人脸、嵌入或模型会话。
控制库故障只影响 ``/health/ready``，不阻止进程提供 ``/health/live``。
"""

from __future__ import annotations

import argparse
import getpass
import sys
from contextlib import asynccontextmanager
from pathlib import Path  # noqa: F401 - 同时用于 SPA 目录解析
from typing import AsyncIterator

import httpx
from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse

from sentrix.control.auth import (
    AuthContext,
    build_router as build_auth_router,
    current_auth,
    require_admin,
    require_csrf,
)
from sentrix.control import admin as admin_routes
from sentrix.control import feedback as feedback_routes
from sentrix.control.config import ControlSettings
from sentrix.control.internal_model import router as internal_model_router
from sentrix.control.metrics import collect_host_snapshot
from sentrix.control.model_gate import ModelClientTable, ModelGate, ModelTarget
from sentrix.control.monitor import Monitor
from sentrix.control.proxy import (
    PROXY_METHODS,
    RouteTable,
    denied_for_user,
    forward,
)
from sentrix.control.store import CONTROL_MIGRATIONS_DIR, AccountError, ControlStore
from sentrix.infrastructure.persistence.database import Database

CONTROL_PREFIX = "/control/v1"


def create_app(settings: ControlSettings | None = None) -> FastAPI:
    settings = settings or ControlSettings()
    settings.ensure_directories()

    database = Database(settings.db_path, CONTROL_MIGRATIONS_DIR)
    store = ControlStore(database)
    store.migrate()

    routes = RouteTable.load(settings.users_file)
    gate = ModelGate(
        ModelTarget(
            upstream_url=settings.model_upstream_url,
            model_id=settings.model_id,
            slots=settings.model_slots,
        )
    )
    model_clients = ModelClientTable.load(settings.model_clients_file)
    monitor = Monitor(settings=settings, store=store, routes=routes, gate=gate)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        client = httpx.AsyncClient(
            timeout=httpx.Timeout(connect=10.0, read=None, write=None, pool=30.0),
            limits=httpx.Limits(max_connections=200, max_keepalive_connections=50),
            follow_redirects=False,
        )
        app.state.http_client = client
        monitor.start()
        try:
            yield
        finally:
            monitor.stop()
            await client.aclose()

    app = FastAPI(title="Sentrix Control", docs_url=None, redoc_url=None, lifespan=lifespan)
    app.state.control_settings = settings
    app.state.control_store = store
    app.state.route_table = routes
    app.state.model_gate = gate
    app.state.model_client_table = model_clients
    app.state.monitor = monitor

    router = APIRouter(prefix=CONTROL_PREFIX)

    @router.get("/health/live")
    def live() -> dict[str, object]:
        return {"status": "ok", "service": "sentrix-control", "build_sha": settings.build_sha}

    @router.get("/health/ready")
    def ready() -> JSONResponse:
        database_ok = store.ping()
        route_ok = routes.ready
        healthy = database_ok and route_ok
        clients_ok = model_clients.ready
        healthy = healthy and clients_ok
        payload: dict[str, object] = {
            "status": "ready" if healthy else "not_ready",
            "build_sha": settings.build_sha,
            "checks": {
                "control_db": "ok" if database_ok else "unavailable",
                "routes": "ok" if route_ok else routes.error or "unavailable",
                "model_clients": "ok" if clients_ok else model_clients.error,
            },
        }
        return JSONResponse(payload, status_code=200 if healthy else 503)

    @router.get("/diagnostics")
    def diagnostics(_: AuthContext = Depends(require_admin)) -> dict[str, object]:
        database_ok = store.ping()
        payload = settings.diagnostics(route_users=len(routes), ready=database_ok and routes.ready)
        payload["schema_version"] = store.schema_version() if database_ok else ""
        payload["model"] = gate.describe()
        payload["host"] = collect_host_snapshot().as_dict()
        return payload

    router.include_router(build_auth_router())
    if settings.admin_only:
        # 独立管理入口：只装管理侧接口，不转发用户 API，也不提供用户反馈提交。
        router.include_router(feedback_routes.admin_router)
    else:
        router.include_router(feedback_routes.router)
        router.include_router(feedback_routes.admin_router)
    router.include_router(admin_routes.router)
    app.include_router(router)
    if not settings.admin_only:
        app.include_router(internal_model_router)

        @app.api_route(
            "/api/v1/{path:path}",
            methods=PROXY_METHODS,
            include_in_schema=False,
        )
        async def proxy_api(
            request: Request,
            path: str,
            auth: AuthContext = Depends(current_auth),
        ):
            del path
            require_csrf(request, auth)
            if auth.account.role != "user":
                raise HTTPException(
                    status_code=403, detail="administrator is not routed to a user API"
                )
            if denied_for_user(request.method, request.url.path):
                raise HTTPException(
                    status_code=403, detail="route not available in experience mode"
                )
            route = routes.get(auth.account.id)
            if route is None:
                raise HTTPException(status_code=503, detail="user API is not configured")
            return await forward(request, route, request.app.state.http_client)

    # 同一个 React 构建由控制面提供；API 前缀已在上面的路由里处理，SPA 只兜底其余 GET。
    dist = Path(__file__).resolve().parents[5] / "apps" / "web" / "dist"
    if (dist / "index.html").is_file():

        @app.get("/{full_path:path}", include_in_schema=False)
        def spa(full_path: str) -> FileResponse:
            if full_path.startswith(("api/", "control/", "internal/")) or full_path in {
                "api",
                "control",
                "internal",
            }:
                raise HTTPException(status_code=404, detail="not found")
            root = dist.resolve()
            candidate = (root / full_path).resolve()
            if full_path and candidate.is_file() and root in candidate.parents:
                # 带哈希的构建产物可长缓存；index.html 必须每次校验，避免旧 JS。
                cache = (
                    "public, max-age=31536000, immutable"
                    if full_path.startswith("assets/")
                    else "no-store"
                )
                return FileResponse(candidate, headers={"Cache-Control": cache})
            return FileResponse(root / "index.html", headers={"Cache-Control": "no-store"})

    return app


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m sentrix.control.main")
    subcommands = parser.add_subparsers(dest="command", required=True)

    seed = subcommands.add_parser("seed-user", help="创建预置账号")
    seed.add_argument("account_id")
    seed.add_argument("--display-name", default="")
    seed.add_argument("--role", choices=("user", "admin"), default="user")
    seed.add_argument("--api-url", default="")
    seed.add_argument("--data-root", default="")
    seed.add_argument("--password-file", default="")

    reset = subcommands.add_parser("reset-password", help="重置口令并撤销该账号全部会话")
    reset.add_argument("account_id")
    reset.add_argument("--password-file", default="")

    revoke = subcommands.add_parser("revoke-sessions", help="撤销账号全部会话")
    revoke.add_argument("account_id")

    subcommands.add_parser("list-users", help="列出账号（不含口令）")

    args = parser.parse_args(argv)
    settings = ControlSettings()
    settings.ensure_directories()
    store = ControlStore(Database(settings.db_path, CONTROL_MIGRATIONS_DIR))
    store.migrate()

    if args.command == "seed-user":
        password = _read_password(args.password_file)
        try:
            account = store.create_account(
                args.account_id,
                display_name=args.display_name or args.account_id,
                role=args.role,
                password=password,
                api_url=args.api_url,
                data_root=args.data_root,
            )
        except AccountError as error:
            print(f"error: {error}", file=sys.stderr)
            return 2
        store.audit("account.seed", target=account.id, result="ok", detail={"role": account.role})
        print(f"created {account.id} role={account.role}")
        return 0

    if args.command == "reset-password":
        password = _read_password(args.password_file)
        if not store.set_password(args.account_id, password):
            print("error: account not found", file=sys.stderr)
            return 2
        revoked = store.revoke_account_sessions(args.account_id)
        store.audit(
            "account.reset_password", target=args.account_id, result="ok", detail={"revoked": revoked}
        )
        print(f"password reset for {args.account_id}; revoked {revoked} session(s)")
        return 0

    if args.command == "revoke-sessions":
        revoked = store.revoke_account_sessions(args.account_id)
        store.audit(
            "account.revoke_sessions", target=args.account_id, result="ok", detail={"revoked": revoked}
        )
        print(f"revoked {revoked} session(s) for {args.account_id}")
        return 0

    if args.command == "list-users":
        with store.database.connect() as connection:
            rows = connection.execute(
                "SELECT id, display_name, role, disabled, api_url, data_root FROM accounts ORDER BY id"
            ).fetchall()
        for row in rows:
            state = "disabled" if row["disabled"] else "active"
            print(
                f"{row['id']}\t{row['role']}\t{state}\t{row['display_name']}\t"
                f"{row['api_url']}\t{row['data_root']}"
            )
        return 0

    return 2


def _read_password(path: str) -> str:
    if path:
        password_file = Path(path)
        mode = password_file.stat().st_mode & 0o777
        if mode & 0o077:
            raise SystemExit("password file must not be readable by group or others (0600)")
        password = password_file.read_text(encoding="utf-8").rstrip("\n")
    else:
        password = getpass.getpass("password: ")
        confirmation = getpass.getpass("confirm: ")
        if password != confirmation:
            raise SystemExit("passwords do not match")
    if len(password) < 8:
        raise SystemExit("password must be at least 8 characters")
    return password


if __name__ == "__main__":  # pragma: no cover - 运维入口
    raise SystemExit(main())
