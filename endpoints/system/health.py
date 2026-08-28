
from fastapi import APIRouter
import time
import os
from datetime import datetime
from typing import Dict, Any, List
import httpx
import psutil
from models.tags import Tags
from shared.version import VERSION, GIT_SHA

router = APIRouter(tags=[Tags.SISTEMA])

THRESHOLDS = {
    "postgresql": {"healthy": 200, "degraded": 1000},
    "redis": {"healthy": 50, "degraded": 200},
    "pdfcomposer": {"healthy": 500, "degraded": 2000},
    "notary": {"healthy": 500, "degraded": 2000},
    "cloudflare_r2": {"healthy": 1000, "degraded": 3000}
}

SYSTEM_THRESHOLDS = {
    "memory_warning": 80,
    "memory_critical": 90,
    "cpu_warning": 70,
    "cpu_critical": 90,
    "disk_warning": 85,
    "disk_critical": 95
}


async def check_postgresql() -> Dict[str, Any]:
    from database import fetch_val, get_pool, DB_HOST, DB_PORT, DB_NAME, USE_PGBOUNCER

    try:
        start_time = time.time()
        result = await fetch_val("SELECT 1", schema_name="public")
        latency_ms = (time.time() - start_time) * 1000

        if latency_ms < THRESHOLDS["postgresql"]["healthy"]:
            status = "healthy"
        elif latency_ms < THRESHOLDS["postgresql"]["degraded"]:
            status = "degraded"
        else:
            status = "unhealthy"

        pool_info = "N/A"
        try:
            p = get_pool()
            pool_info = f"Active (min={p.get_min_size()}, max={p.get_max_size()}, size={p.get_size()})"
        except Exception:
            pool_info = "Active (details unavailable)"

        connection_type = "PgBouncer" if USE_PGBOUNCER else "Direct PostgreSQL"

        return {
            "status": status,
            "latency_ms": round(latency_ms, 2),
            "threshold": THRESHOLDS["postgresql"],
            "details": {
                "connection_type": connection_type,
                "host": DB_HOST,
                "port": DB_PORT,
                "database": DB_NAME,
                "pool": pool_info,
                "query": "SELECT 1",
                "result": "OK" if result == 1 else "FAIL"
            }
        }
    except Exception as e:
        return {
            "status": "unhealthy",
            "latency_ms": None,
            "threshold": THRESHOLDS["postgresql"],
            "details": {
                "connection_type": "PgBouncer" if USE_PGBOUNCER else "Direct PostgreSQL",
                "host": DB_HOST,
                "port": DB_PORT,
                "database": DB_NAME,
                "error": str(e)
            }
        }


async def check_redis() -> Dict[str, Any]:
    from services.cache import redis_client

    redis_url = os.getenv("REDIS_URL")

    if not redis_url:
        return {
            "status": "not_configured",
            "latency_ms": None,
            "threshold": THRESHOLDS["redis"],
            "details": {
                "configured": False,
                "message": "Redis cache is optional and currently not configured"
            }
        }

    if redis_client is None:
        return {
            "status": "unhealthy",
            "latency_ms": None,
            "threshold": THRESHOLDS["redis"],
            "details": {
                "configured": True,
                "connected": False,
                "error": "Redis client failed to initialize"
            }
        }

    try:
        start_time = time.time()
        pong = redis_client.ping()
        latency_ms = (time.time() - start_time) * 1000

        if latency_ms < THRESHOLDS["redis"]["healthy"]:
            status = "healthy"
        elif latency_ms < THRESHOLDS["redis"]["degraded"]:
            status = "degraded"
        else:
            status = "unhealthy"

        return {
            "status": status,
            "latency_ms": round(latency_ms, 2),
            "threshold": THRESHOLDS["redis"],
            "details": {
                "configured": True,
                "connected": True,
                "ping": "PONG" if pong else "FAIL"
            }
        }
    except Exception as e:
        return {
            "status": "unhealthy",
            "latency_ms": None,
            "threshold": THRESHOLDS["redis"],
            "details": {
                "configured": True,
                "connected": False,
                "error": str(e)
            }
        }


async def check_pdfcomposer() -> Dict[str, Any]:
    url = os.getenv('PDFCOMPOSER_URL')
    api_key = os.getenv('PDFCOMPOSER_API_KEY')

    if not url or not api_key:
        return {
            "status": "misconfigured",
            "latency_ms": None,
            "threshold": THRESHOLDS["pdfcomposer"],
            "details": {
                "configured": False,
                "error": "PDFCOMPOSER_URL or PDFCOMPOSER_API_KEY not set"
            }
        }

    try:
        async with httpx.AsyncClient() as client:
            start_time = time.time()
            response = await client.get(
                f"{url}/health",
                headers={"X-API-Key": api_key},
                timeout=5.0
            )
            latency_ms = (time.time() - start_time) * 1000

            if response.status_code == 200:
                if latency_ms < THRESHOLDS["pdfcomposer"]["healthy"]:
                    status = "healthy"
                elif latency_ms < THRESHOLDS["pdfcomposer"]["degraded"]:
                    status = "degraded"
                else:
                    status = "degraded"
            else:
                status = "unhealthy"

            return {
                "status": status,
                "latency_ms": round(latency_ms, 2),
                "threshold": THRESHOLDS["pdfcomposer"],
                "details": {
                    "url": url,
                    "configured": True,
                    "reachable": True,
                    "http_status": response.status_code
                }
            }
    except httpx.TimeoutException:
        return {
            "status": "timeout",
            "latency_ms": None,
            "threshold": THRESHOLDS["pdfcomposer"],
            "details": {
                "url": url,
                "configured": True,
                "reachable": False,
                "error": "Request timeout (>5s)"
            }
        }
    except Exception as e:
        return {
            "status": "unhealthy",
            "latency_ms": None,
            "threshold": THRESHOLDS["pdfcomposer"],
            "details": {
                "url": url,
                "configured": True,
                "reachable": False,
                "error": str(e)
            }
        }


async def check_notary() -> Dict[str, Any]:
    url = os.getenv('NOTARY_URL')
    api_key = os.getenv('NOTARY_API_KEY')

    if not url or not api_key:
        return {
            "status": "misconfigured",
            "latency_ms": None,
            "threshold": THRESHOLDS["notary"],
            "details": {
                "configured": False,
                "error": "NOTARY_URL or NOTARY_API_KEY not set"
            }
        }

    try:
        async with httpx.AsyncClient() as client:
            start_time = time.time()
            response = await client.get(
                f"{url}/health",
                headers={"X-API-Key": api_key},
                timeout=5.0
            )
            latency_ms = (time.time() - start_time) * 1000

            if response.status_code == 200:
                if latency_ms < THRESHOLDS["notary"]["healthy"]:
                    status = "healthy"
                elif latency_ms < THRESHOLDS["notary"]["degraded"]:
                    status = "degraded"
                else:
                    status = "degraded"
            else:
                status = "unhealthy"

            return {
                "status": status,
                "latency_ms": round(latency_ms, 2),
                "threshold": THRESHOLDS["notary"],
                "details": {
                    "url": url,
                    "configured": True,
                    "reachable": True,
                    "http_status": response.status_code
                }
            }
    except httpx.TimeoutException:
        return {
            "status": "timeout",
            "latency_ms": None,
            "threshold": THRESHOLDS["notary"],
            "details": {
                "url": url,
                "configured": True,
                "reachable": False,
                "error": "Request timeout (>5s)"
            }
        }
    except Exception as e:
        return {
            "status": "unhealthy",
            "latency_ms": None,
            "threshold": THRESHOLDS["notary"],
            "details": {
                "url": url,
                "configured": True,
                "reachable": False,
                "error": str(e)
            }
        }


async def check_cloudflare_r2() -> Dict[str, Any]:
    try:
        from botocore.exceptions import ClientError
        from services.storage.cloudflare import get_r2_client

        endpoint = os.getenv('CF_R2_ENDPOINT')

        if not endpoint:
            return {
                "status": "misconfigured",
                "latency_ms": None,
                "threshold": THRESHOLDS["cloudflare_r2"],
                "details": {
                    "configured": False,
                    "error": "CF_R2_ENDPOINT not configured"
                }
            }

        r2_client = get_r2_client()

        if r2_client._client is None:
            return {
                "status": "misconfigured",
                "latency_ms": None,
                "threshold": THRESHOLDS["cloudflare_r2"],
                "details": {
                    "endpoint": endpoint,
                    "configured": False,
                    "error": "R2 client failed to initialize (check credentials)"
                }
            }

        start_time = time.time()
        try:
            response = r2_client._client.list_buckets()
            latency_ms = (time.time() - start_time) * 1000
            bucket_names = [b["Name"] for b in response.get("Buckets", [])]
            extra_details = {
                "buckets_found": len(bucket_names),
                "buckets": bucket_names
            }
        except ClientError as e:
            latency_ms = (time.time() - start_time) * 1000
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code == "AccessDenied":
                extra_details = {
                    "note": "Credentials valid (authenticated). Token lacks ListBuckets permission - this is expected for Object Read & Write tokens.",
                }
            else:
                raise

        if latency_ms < THRESHOLDS["cloudflare_r2"]["healthy"]:
            status = "healthy"
        elif latency_ms < THRESHOLDS["cloudflare_r2"]["degraded"]:
            status = "degraded"
        else:
            status = "degraded"

        return {
            "status": status,
            "latency_ms": round(latency_ms, 2),
            "threshold": THRESHOLDS["cloudflare_r2"],
            "details": {
                "endpoint": endpoint,
                "configured": True,
                "reachable": True,
                **extra_details
            }
        }
    except Exception as e:
        return {
            "status": "unhealthy",
            "latency_ms": None,
            "threshold": THRESHOLDS["cloudflare_r2"],
            "details": {
                "endpoint": os.getenv('CF_R2_ENDPOINT', 'not set'),
                "configured": True,
                "reachable": False,
                "error": str(e)
            }
        }


def get_system_metrics() -> Dict[str, Any]:
    try:
        memory = psutil.virtual_memory()
        memory_metrics = {
            "total_mb": round(memory.total / (1024 * 1024), 2),
            "used_mb": round(memory.used / (1024 * 1024), 2),
            "available_mb": round(memory.available / (1024 * 1024), 2),
            "percent_used": round(memory.percent, 2)
        }

        cpu_percent = psutil.cpu_percent(interval=0.1)
        cpu_metrics = {
            "percent_used": round(cpu_percent, 2),
            "cores": psutil.cpu_count()
        }

        disk = psutil.disk_usage('/')
        disk_metrics = {
            "total_gb": round(disk.total / (1024 * 1024 * 1024), 2),
            "used_gb": round(disk.used / (1024 * 1024 * 1024), 2),
            "available_gb": round(disk.free / (1024 * 1024 * 1024), 2),
            "percent_used": round(disk.percent, 2)
        }

        process = psutil.Process()
        process_metrics = {
            "pid": process.pid,
            "memory_mb": round(process.memory_info().rss / (1024 * 1024), 2),
            "threads": process.num_threads(),
            "open_files": len(process.open_files())
        }

        return {
            "memory": memory_metrics,
            "cpu": cpu_metrics,
            "disk": disk_metrics,
            "process": process_metrics
        }
    except Exception as e:
        return {
            "error": f"Failed to get system metrics: {str(e)}"
        }


def generate_alerts(services: Dict[str, Any], system_metrics: Dict[str, Any]) -> List[Dict[str, str]]:
    alerts = []

    for service_name, service_data in services.items():
        status = service_data.get("status")
        if status == "unhealthy":
            alerts.append({
                "level": "CRITICAL",
                "service": service_name,
                "message": f"{service_name} is unhealthy"
            })
        elif status == "degraded":
            alerts.append({
                "level": "WARNING",
                "service": service_name,
                "message": f"{service_name} is degraded (high latency)"
            })
        elif status == "timeout":
            alerts.append({
                "level": "CRITICAL",
                "service": service_name,
                "message": f"{service_name} request timeout"
            })
        elif status == "misconfigured":
            alerts.append({
                "level": "CRITICAL",
                "service": service_name,
                "message": f"{service_name} is misconfigured"
            })

    if "memory" in system_metrics:
        memory_percent = system_metrics["memory"]["percent_used"]
        if memory_percent >= SYSTEM_THRESHOLDS["memory_critical"]:
            alerts.append({
                "level": "CRITICAL",
                "service": "system_memory",
                "message": f"Memory usage critical: {memory_percent}%"
            })
        elif memory_percent >= SYSTEM_THRESHOLDS["memory_warning"]:
            alerts.append({
                "level": "WARNING",
                "service": "system_memory",
                "message": f"Memory usage high: {memory_percent}%"
            })

    if "cpu" in system_metrics:
        cpu_percent = system_metrics["cpu"]["percent_used"]
        if cpu_percent >= SYSTEM_THRESHOLDS["cpu_critical"]:
            alerts.append({
                "level": "CRITICAL",
                "service": "system_cpu",
                "message": f"CPU usage critical: {cpu_percent}%"
            })
        elif cpu_percent >= SYSTEM_THRESHOLDS["cpu_warning"]:
            alerts.append({
                "level": "WARNING",
                "service": "system_cpu",
                "message": f"CPU usage high: {cpu_percent}%"
            })

    if "disk" in system_metrics:
        disk_percent = system_metrics["disk"]["percent_used"]
        if disk_percent >= SYSTEM_THRESHOLDS["disk_critical"]:
            alerts.append({
                "level": "CRITICAL",
                "service": "system_disk",
                "message": f"Disk usage critical: {disk_percent}%"
            })
        elif disk_percent >= SYSTEM_THRESHOLDS["disk_warning"]:
            alerts.append({
                "level": "WARNING",
                "service": "system_disk",
                "message": f"Disk usage high: {disk_percent}%"
            })

    return alerts


@router.get("/livez")
async def liveness_probe():
    """
    Liveness Probe - Endpoint simple para Fly.io health checks.

    Retorna 200 OK si el servicio está vivo (sin verificar dependencias).
    Para verificación completa de dependencias, usar /api/v1/system/health.
    """
    return {"status": "ok", "timestamp": datetime.now().isoformat(), "version": VERSION, "commit": GIT_SHA}


@router.get("/api/v1/system/health")
async def health_check():
    """
    Health Check Endpoint - Verificar estado de todos los servicios

    Endpoint público (sin autenticación) para monitoreo de servicios.

    Verifica:
    - PostgreSQL (base de datos)
    - Redis (cache opcional)
    - PDFComposer (generación de PDFs)
    - Notary (firma digital)
    - Cloudflare R2 (almacenamiento)
    - Métricas del sistema (CPU, memoria, disco)

    Returns:
        200 OK: Todos los servicios healthy
        207 Multi-Status: Algunos servicios degraded
        503 Service Unavailable: Servicios críticos unhealthy
    """

    import asyncio

    postgresql_check, redis_check, pdfcomposer_check, notary_check, r2_check = await asyncio.gather(
        check_postgresql(),
        check_redis(),
        check_pdfcomposer(),
        check_notary(),
        check_cloudflare_r2()
    )

    services = {
        "postgresql": postgresql_check,
        "redis": redis_check,
        "pdfcomposer": pdfcomposer_check,
        "notary": notary_check,
        "cloudflare_r2": r2_check
    }

    system_metrics = get_system_metrics()

    statuses = [s["status"] for s in services.values()]
    summary = {
        "total_services": len(services),
        "healthy": statuses.count("healthy"),
        "degraded": statuses.count("degraded"),
        "unhealthy": statuses.count("unhealthy"),
        "timeout": statuses.count("timeout"),
        "misconfigured": statuses.count("misconfigured"),
        "not_configured": statuses.count("not_configured")
    }

    alerts = generate_alerts(services, system_metrics)

    critical_statuses = ["unhealthy", "timeout", "misconfigured"]
    warning_statuses = ["degraded"]

    if any(s["status"] in critical_statuses for s in services.values() if s["status"] != "not_configured"):
        system_status = "unhealthy"
        http_status = 503
    elif any(s["status"] in warning_statuses for s in services.values()):
        system_status = "degraded"
        http_status = 207
    else:
        system_status = "healthy"
        http_status = 200

    response = {
        "status": system_status,
        "timestamp": datetime.now().isoformat(),
        "version": "1.2",
        "app_version": VERSION,
        "commit": GIT_SHA,
        "environment": os.getenv("SENTRY_ENV") or os.getenv("FLY_APP_NAME") or "local",
        "services": services,
        "system_metrics": system_metrics,
        "summary": summary,
        "alerts": alerts
    }

    if http_status != 200:
        from fastapi.responses import JSONResponse
        return JSONResponse(content=response, status_code=http_status)

    return response
