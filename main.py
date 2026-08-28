from dotenv import load_dotenv

load_dotenv()

from shared.logging import setup_logging, get_logger
setup_logging()

from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import asyncpg
from database import init_pool, close_pool
import asyncio
import importlib
import os
import time
from models.tags import tag_metadata
from middleware.tenant_middleware import TenantMiddleware
from middleware.rate_limit import RateLimitMiddleware
from middleware.host_filter import HostFilterMiddleware
from middleware.security_headers import SecurityHeadersMiddleware
from shared.error_alerts import report_error
from shared.exceptions import DatabaseBusyError
from shared.version import VERSION, GIT_SHA
from config.constants import ESCRI_SHUTDOWN_GRACE_SECONDS

main_logger = get_logger(__name__)

ESCRI_WATCHDOG_STALE_SECONDS    = int(os.getenv("ESCRI_WATCHDOG_STALE_SECONDS", "120"))
ESCRI_WATCHDOG_INTERVAL_SECONDS = int(os.getenv("ESCRI_WATCHDOG_INTERVAL_SECONDS", "30"))


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_pool()
    from shared.tenant_validation import clear_all_cache
    clear_all_cache()
    main_logger.info("[OK] Pool asyncpg inicializado")

    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    from jobs.orphan_inprocess import schedule_orphan_reclaim
    from workers.sweeper_escri import schedule_sweeper_escri
    from workers.sweeper_tad_webhook import schedule_sweeper_tad_webhook
    from jobs.fill_number_gaps_tst import schedule_tst_sweep
    from jobs.reconcile_r2_db import schedule_reconcile_r2_db
    from jobs.retry_failed_publications import schedule_retry_failed_publications
    scheduler = AsyncIOScheduler()
    schedule_orphan_reclaim(scheduler)
    schedule_sweeper_escri(scheduler)
    schedule_sweeper_tad_webhook(scheduler)
    schedule_tst_sweep(scheduler)
    schedule_reconcile_r2_db(scheduler)
    schedule_retry_failed_publications(scheduler)
    scheduler.start()
    app.state.scheduler = scheduler
    main_logger.info(
        "[OK] APScheduler iniciado - orphan_reclaim + sweeper_escri + "
        "sweeper_tad_webhook + tst_sweep + reconcile_r2_db + "
        "retry_failed_publications"
    )

    from workers.escri import EscriWorker

    async def _on_escri_done_async(reason: str) -> None:
        try:
            from shared.alerts import send_alert_mail
            await send_alert_mail(
                subject="[GDI ESCRI] Worker embebido terminó inesperadamente",
                body=(
                    f"El worker ESCRI se detuvo mientras _running=True.\n"
                    f"Motivo: {reason}\n"
                    f"El watchdog intentará recrear la tarea automáticamente."
                ),
            )
        except Exception as _ae:
            main_logger.error("[ESCRI] Alerta muerte worker también falló: %s", _ae)

    def _make_escri_done_callback(worker):
        def _cb(task: asyncio.Task) -> None:
            if not worker._running:
                return
            try:
                exc = task.exception()
            except (asyncio.CancelledError, asyncio.InvalidStateError):
                exc = None
            reason = str(exc) if exc else "worker_silent_exit (sin excepción)"
            main_logger.critical(
                "[ESCRI] Worker terminó inesperadamente con _running=True: %s", reason,
            )
            asyncio.get_event_loop().call_soon_threadsafe(
                lambda: asyncio.ensure_future(_on_escri_done_async(reason))
            )
        return _cb

    def _spawn_escri_worker() -> None:
        w = EscriWorker()
        t = asyncio.create_task(w.run())
        t.add_done_callback(_make_escri_done_callback(w))
        app.state.escri_worker = w
        app.state.escri_task = t

    _spawn_escri_worker()
    app.state.escri_restarts = 0
    main_logger.info("[OK] Worker ESCRI async iniciado (embebido)")

    async def _escri_watchdog() -> None:
        while True:
            await asyncio.sleep(ESCRI_WATCHDOG_INTERVAL_SECONDS)
            worker = getattr(app.state, "escri_worker", None)
            task = getattr(app.state, "escri_task", None)
            if worker is None or task is None:
                continue
            last_hb = worker.last_heartbeat_at
            stale_for = (time.monotonic() - last_hb) if last_hb > 0.0 else 0.0
            task_dead = task.done()
            if not task_dead and stale_for <= ESCRI_WATCHDOG_STALE_SECONDS:
                continue
            main_logger.critical(
                "[ESCRI-WD] Worker %s (heartbeat hace %.0fs, task_done=%s) — recreando tarea",
                "muerto" if task_dead else "trabado", stale_for, task_dead,
            )
            try:
                worker.stop()
                task.cancel()
                try:
                    await asyncio.wait_for(task, timeout=15)
                except BaseException:
                    pass
            except Exception as _stop_err:
                main_logger.error("[ESCRI-WD] error deteniendo worker viejo: %s", _stop_err)
            _spawn_escri_worker()
            app.state.escri_restarts = getattr(app.state, "escri_restarts", 0) + 1
            main_logger.info(
                "[ESCRI-WD] Worker ESCRI recreado (restart #%d)", app.state.escri_restarts
            )

    app.state.escri_watchdog_task = asyncio.create_task(_escri_watchdog())
    main_logger.info(
        "[OK] Watchdog ESCRI iniciado (stale>%ds o task muerta → recrea tarea)",
        ESCRI_WATCHDOG_STALE_SECONDS,
    )

    from workers.tad_webhook_worker import TadWebhookWorker
    tad_webhook_worker = TadWebhookWorker()
    app.state.tad_webhook_worker = tad_webhook_worker
    app.state.tad_webhook_task = asyncio.create_task(tad_webhook_worker.run())
    main_logger.info("[OK] Worker webhook TAD iniciado (embebido)")

    asyncio.create_task(_warm_up_services())
    main_logger.info("[OK] Backend GDI iniciado correctamente con sistema de expedientes")

    yield

    if hasattr(app.state, "escri_watchdog_task"):
        app.state.escri_watchdog_task.cancel()
        try:
            await app.state.escri_watchdog_task
        except asyncio.CancelledError:
            pass
        main_logger.info("[OK] Watchdog ESCRI detenido")

    if hasattr(app.state, "escri_worker"):
        app.state.escri_worker.stop()
    if hasattr(app.state, "escri_task"):
        app.state.escri_task.cancel()
        try:
            await asyncio.wait_for(
                app.state.escri_task, timeout=ESCRI_SHUTDOWN_GRACE_SECONDS
            )
        except (asyncio.CancelledError, asyncio.TimeoutError):
            pass
    main_logger.info("[OK] Worker ESCRI detenido")

    if hasattr(app.state, "tad_webhook_worker"):
        app.state.tad_webhook_worker.stop()
    if hasattr(app.state, "tad_webhook_task"):
        app.state.tad_webhook_task.cancel()
        try:
            await app.state.tad_webhook_task
        except asyncio.CancelledError:
            pass
    main_logger.info("[OK] Worker webhook TAD detenido")

    if hasattr(app.state, "scheduler"):
        app.state.scheduler.shutdown(wait=False)
        main_logger.info("[OK] APScheduler detenido correctamente")
    await close_pool()
    main_logger.info("[OK] Pool asyncpg cerrado")


_ENVIRONMENT = os.getenv("ENVIRONMENT", "production")
_is_production = _ENVIRONMENT == "production"

app = FastAPI(
    title="GDI Backend",
    description="""
    API para gestión de documentos institucionales.

    Esta API permite:
    - Gestión de documentos en diferentes estados (borradores y oficiales)
    - Consulta de documentos por usuario con filtros avanzados y paginación
    - Recuperación de metadatos del sistema (estados visuales, tipos de documentos)
    """,
    version=VERSION,
    contact={
        "name": "Equipo de Desarrollo GDI",
        "url": "https://municipalidad-ejemplo.cl/soporte",
        "email": "soporte@municipalidad-ejemplo.cl",
    },
    license_info={
        "name": "Licencia Propietaria",
        "url": "https://municipalidad-ejemplo.cl/licencia",
    },
    openapi_tags=tag_metadata,
    swagger_ui_parameters={
        "persistAuthorization": True,
    },
    lifespan=lifespan,
    docs_url=None if _is_production else "/docs",
    redoc_url=None if _is_production else "/redoc",
    openapi_url=None if _is_production else "/openapi.json",
)

_ORIGENES_LOCALES = [
    "http://localhost:3000",
    "http://localhost:3001",
    "http://127.0.0.1:3000",
    "http://127.0.0.1:3001",
]


def _origen_valido(url: str) -> bool:
    from urllib.parse import urlparse

    if "*" in url:
        return False
    try:
        u = urlparse(url)
    except ValueError:
        return False
    if u.scheme not in ("http", "https") or not u.netloc:
        return False
    return u.path in ("", "/") and not u.query and not u.fragment and not u.params


allowed_origins = [] if _is_production else list(_ORIGENES_LOCALES)

frontend_urls = os.getenv("FRONTEND_URL", "")
for url in frontend_urls.split(","):
    url = url.strip().rstrip("/")
    if not url:
        continue
    if not _origen_valido(url):
        main_logger.error(
            f"CORS: origen descartado por invalido en FRONTEND_URL: {url!r} "
            "(se espera solo esquema://host[:puerto])"
        )
        continue
    if url not in allowed_origins:
        allowed_origins.append(url)

if not allowed_origins:
    main_logger.warning(
        "CORS: la whitelist quedo VACIA (revisar FRONTEND_URL). "
        "El navegador va a bloquear al front."
    )

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=[
        "Authorization",
        "Content-Type",
        "Accept",
        "Origin",
        "X-Requested-With",
        "X-User-ID",
        "X-Tenant-Schema",
        "X-API-Key",
    ],
)

app.add_middleware(RateLimitMiddleware)

app.add_middleware(TenantMiddleware)
main_logger.info("TenantMiddleware registrado correctamente")

app.add_middleware(HostFilterMiddleware)
main_logger.info("HostFilterMiddleware registrado correctamente")

app.add_middleware(SecurityHeadersMiddleware)
main_logger.info("SecurityHeadersMiddleware registrado correctamente")


@app.exception_handler(asyncpg.PostgresError)
async def asyncpg_error_handler(request: Request, exc: asyncpg.PostgresError) -> JSONResponse:
    main_logger.error(
        f"[DB ERROR] {type(exc).__name__} en {request.method} {request.url.path}: {exc}",
        exc_info=True,
    )
    report_error(request, exc, kind="DB")
    if isinstance(exc, asyncpg.ForeignKeyViolationError):
        return JSONResponse(
            status_code=409,
            content={"detail": "No se puede realizar esta operación porque el registro está vinculado a otros datos"},
        )
    if isinstance(exc, asyncpg.UniqueViolationError):
        return JSONResponse(
            status_code=409,
            content={"detail": "Ya existe un registro con esos datos"},
        )
    if isinstance(exc, asyncpg.IntegrityConstraintViolationError):
        return JSONResponse(
            status_code=409,
            content={"detail": "No se puede realizar esta operación por restricciones de integridad"},
        )
    return JSONResponse(
        status_code=500,
        content={"detail": "Error al procesar la solicitud"},
    )


@app.exception_handler(DatabaseBusyError)
async def database_busy_handler(request: Request, exc: DatabaseBusyError) -> JSONResponse:
    main_logger.warning(f"[POOL BUSY] {request.method} {request.url.path}")
    return JSONResponse(
        status_code=503,
        headers={"Retry-After": "1"},
        content={"detail": "Servidor ocupado, reintente en unos segundos"},
    )


@app.exception_handler(Exception)
async def generic_error_handler(request: Request, exc: Exception) -> JSONResponse:
    if isinstance(exc, HTTPException):
        raise exc

    if isinstance(exc, DatabaseBusyError):
        main_logger.warning(f"[POOL BUSY/mw] {request.method} {request.url.path}")
        return JSONResponse(
            status_code=503,
            headers={"Retry-After": "1"},
            content={"detail": "Servidor ocupado, reintente en unos segundos"},
        )

    main_logger.error(
        f"[UNHANDLED] {type(exc).__name__} en {request.method} {request.url.path}: {exc}",
        exc_info=True,
    )
    report_error(request, exc, kind="UNHANDLED")
    return JSONResponse(
        status_code=500,
        content={"detail": "Error interno del servidor"},
    )


if not _is_production:
    @app.get("/_debug/boom", include_in_schema=False)
    async def _debug_boom():
        raise RuntimeError("Error de prueba (DIY error-mail): validacion del pipeline de alertas")


def include_endpoints(app):
    endpoint_categories = ['auth', 'documents', 'users', 'system', 'cases', 'sectors', 'dashboard', 'notes', 'memos', 'ccoo', 'rlm', 'search', 'digital_signature', 'citizens', 'home']
    
    for category in endpoint_categories:
        category_path = f'endpoints.{category}'
        category_dir = f'endpoints/{category}'
        
        if not os.path.isdir(category_dir):
            continue
        
        if category == 'documents':
            router_module = importlib.import_module(f"{category_path}.router")
            if hasattr(router_module, 'documents_router'):
                app.include_router(router_module.documents_router)
        elif category == 'cases':
            router_module = importlib.import_module(f"{category_path}.router")
            if hasattr(router_module, 'cases_router'):
                app.include_router(router_module.cases_router)
        elif category == 'sectors':
            router_module = importlib.import_module(f"{category_path}.router")
            if hasattr(router_module, 'router'):
                app.include_router(router_module.router)
        elif category == 'dashboard':
            router_module = importlib.import_module(f"{category_path}.router")
            if hasattr(router_module, 'dashboard_router'):
                app.include_router(router_module.dashboard_router)
        elif category == 'notes':
            router_module = importlib.import_module(f"{category_path}.router")
            if hasattr(router_module, 'router'):
                app.include_router(router_module.router)
        elif category == 'memos':
            router_module = importlib.import_module(f"{category_path}.router")
            if hasattr(router_module, 'router'):
                app.include_router(router_module.router)
        elif category == 'ccoo':
            router_module = importlib.import_module(f"{category_path}.router")
            if hasattr(router_module, 'router'):
                app.include_router(router_module.router)
        elif category == 'rlm':
            router_module = importlib.import_module(f"{category_path}.router")
            if hasattr(router_module, 'rlm_router'):
                app.include_router(router_module.rlm_router)
        elif category == 'search':
            router_module = importlib.import_module(f"{category_path}.router")
            if hasattr(router_module, 'router'):
                app.include_router(router_module.router)
        elif category == 'digital_signature':
            router_module = importlib.import_module(f"{category_path}.router")
            if hasattr(router_module, 'digital_signature_router'):
                app.include_router(router_module.digital_signature_router)
        elif category == 'home':
            router_module = importlib.import_module(f"{category_path}.router")
            if hasattr(router_module, 'home_router'):
                app.include_router(router_module.home_router)
        else:
            for file in os.listdir(category_dir):
                if file.endswith('.py') and file != '__init__.py' and file != 'router.py':
                    module_name = f"{category_path}.{file[:-3]}"
                    module = importlib.import_module(module_name)
                    if hasattr(module, 'router'):
                        app.include_router(module.router)

include_endpoints(app)

def _to_public_url(internal_url: str | None) -> str | None:
    if not internal_url:
        return None
    try:
        host = internal_url.split("//", 1)[1]
        if ".internal" not in host:
            return internal_url.rstrip("/")
        app_name = host.split(".internal", 1)[0]
        return f"https://{app_name}.fly.dev"
    except (IndexError, AttributeError):
        return None


async def _warm_up_services():
    import httpx

    services = {
        "AgenteLANG": _to_public_url(os.getenv("AGENT_URL")),
    }
    async with httpx.AsyncClient(timeout=10.0) as client:
        for name, url in services.items():
            if not url:
                continue
            try:
                resp = await client.get(f"{url}/health")
                main_logger.info(f"[WARM-UP] {name}: {resp.status_code}")
            except Exception as e:
                main_logger.warning(f"[WARM-UP] {name}: no responde ({e.__class__.__name__})")


@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    """Endpoint para manejar solicitudes de favicon de los navegadores"""
    return {"message": "No favicon configured"}


@app.get("/health", include_in_schema=False)
async def health_check():
    escri_info: dict = {}
    if hasattr(app.state, "escri_worker") and hasattr(app.state, "escri_task"):
        worker = app.state.escri_worker
        task   = app.state.escri_task
        task_done = task.done() if task is not None else True
        last_hb = worker.last_heartbeat_at
        hb_ago = round(time.monotonic() - last_hb, 1) if last_hb > 0.0 else None
        stale = hb_ago is not None and hb_ago > ESCRI_WATCHDOG_STALE_SECONDS
        escri_info = {
            "alive": worker._running and not task_done and not stale,
            "last_heartbeat_seconds_ago": hb_ago,
            "stale": stale,
            "restarts": getattr(app.state, "escri_restarts", 0),
        }
    return {"status": "ok", "version": VERSION, "commit": GIT_SHA, "escri_worker": escri_info}

if __name__ == "__main__":
    import uvicorn
    import os

    is_production = os.getenv("PORT") is not None
    default_host = "0.0.0.0" if is_production else "127.0.0.1"

    host = os.getenv("HOST", default_host)
    port = int(os.getenv("PORT", 8000))
    reload = os.getenv("RELOAD", "true" if not is_production else "false").lower() == "true"

    uvicorn.run("main:app", host=host, port=port, reload=reload)