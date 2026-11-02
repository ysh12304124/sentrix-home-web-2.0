"""控制服务 app factory。

启动时不构造产品 ``Container``：不碰 Qdrant、人脸、嵌入或模型会话。
控制库故障只影响 ``/health/ready``，不阻止进程提供 ``/health/live``。
"""

from __future__ import annotations

from fastapi import APIRouter, FastAPI
from fastapi.responses import JSONResponse

from sentrix.control.config import ControlSettings
from sentrix.control.metrics import collect_host_snapshot
from sentrix.control.model_gate import ModelGate, ModelTarget
from sentrix.control.proxy import RouteTable
from sentrix.control.store import CONTROL_MIGRATIONS_DIR, ControlStore
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

    app = FastAPI(title="Sentrix Control", docs_url=None, redoc_url=None)
    app.state.control_settings = settings
    app.state.control_store = store
    app.state.route_table = routes
    app.state.model_gate = gate

    router = APIRouter(prefix=CONTROL_PREFIX)

    @router.get("/health/live")
    def live() -> dict[str, object]:
        return {"status": "ok", "service": "sentrix-control", "build_sha": settings.build_sha}

    @router.get("/health/ready")
    def ready() -> JSONResponse:
        database_ok = store.ping()
        route_ok = routes.ready
        healthy = database_ok and route_ok
        payload: dict[str, object] = {
            "status": "ready" if healthy else "not_ready",
            "build_sha": settings.build_sha,
            "checks": {
                "control_db": "ok" if database_ok else "unavailable",
                "routes": "ok" if route_ok else routes.error or "unavailable",
            },
        }
        return JSONResponse(payload, status_code=200 if healthy else 503)

    @router.get("/diagnostics")
    def diagnostics() -> dict[str, object]:
        database_ok = store.ping()
        payload = settings.diagnostics(route_users=len(routes), ready=database_ok and routes.ready)
        payload["schema_version"] = store.schema_version() if database_ok else ""
        payload["model"] = gate.describe()
        payload["host"] = collect_host_snapshot().as_dict()
        return payload

    app.include_router(router)
    return app
