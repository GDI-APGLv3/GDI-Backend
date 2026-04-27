"""
Configuración de Gunicorn para PDF Composer API

Este archivo define la configuración del servidor Gunicorn para producción,
optimizado para Fly.io con soporte IPv6 y múltiples workers.
"""
import os
import multiprocessing
import logging

# Bind a IPv6 (que también acepta IPv4) en el puerto proporcionado por Fly.io
port = os.getenv("PORT", "8000")
bind = f"[::]:{port}"

# Número de workers (4 instancias)
workers = int(os.getenv("GUNICORN_WORKERS", "4"))

# Clase de worker (usar UvicornWorker para compatibilidad con FastAPI async)
worker_class = "uvicorn.workers.UvicornWorker"

# Timeout para requests largos (generación de PDFs puede tomar tiempo)
timeout = int(os.getenv("GUNICORN_TIMEOUT", "120"))

# Keepalive para conexiones persistentes
keepalive = 5

# Logging
accesslog = "-"  # Log a stdout
errorlog = "-"   # Log a stderr
loglevel = os.getenv("LOG_LEVEL", "info")

# Worker lifecycle hooks para debugging (opcional)
def on_starting(server):
    """Llamado justo antes de que el master process sea inicializado."""
    server.log.info(f"Iniciando Gunicorn con {workers} workers en {bind}")

def on_reload(server):
    """Llamado cuando el servidor recarga configuración."""
    server.log.info("Recargando configuración de Gunicorn")

def worker_int(worker):
    """Llamado cuando un worker recibe señal SIGINT o SIGQUIT."""
    worker.log.info(f"Worker {worker.pid} interrumpido")

def worker_abort(worker):
    """Llamado cuando un worker es abortado."""
    worker.log.info(f"Worker {worker.pid} abortado")

# Pre/post fork hooks (útil para liberar recursos)
def pre_fork(server, worker):
    """Llamado justo antes de hacer fork del worker."""
    pass

def post_fork(server, worker):
    """Llamado justo después de hacer fork del worker."""
    server.log.info(f"Worker spawned (pid: {worker.pid})")

    # Configurar Better Stack logging en cada worker
    if os.getenv("BETTERSTACK_SOURCE_TOKEN"):
        try:
            from logtail import LogtailHandler
            handler = LogtailHandler(source_token=os.getenv("BETTERSTACK_SOURCE_TOKEN"))

            # Configurar root logger
            root_logger = logging.getLogger()
            root_logger.setLevel(logging.INFO)
            root_logger.addHandler(handler)

            # Configurar gunicorn logger
            gunicorn_logger = logging.getLogger("gunicorn.access")
            gunicorn_logger.addHandler(handler)
            gunicorn_error_logger = logging.getLogger("gunicorn.error")
            gunicorn_error_logger.addHandler(handler)

            server.log.info(f"Better Stack logging configured for worker {worker.pid}")
        except Exception as e:
            server.log.error(f"Failed to configure Better Stack logging: {e}")

def pre_exec(server):
    """Llamado justo antes de hacer fork del master process."""
    server.log.info("Forked master process, preparing for exec")

def when_ready(server):
    """Llamado justo después de que el servidor esté listo."""
    server.log.info(f"Server listo. Escuchando en {bind}")

def worker_exit(server, worker):
    """Llamado justo después de que un worker haya sido exited."""
    server.log.info(f"Worker exited (pid: {worker.pid})")

# Configuración de proceso
proc_name = "pdfcomposer"

# Preload app para mejorar tiempo de inicio
preload_app = False  # False para evitar problemas con recursos compartidos

# Graceful timeout para shutdowns
graceful_timeout = 30

# Max requests por worker (para prevenir memory leaks)
max_requests = int(os.getenv("GUNICORN_MAX_REQUESTS", "300"))
max_requests_jitter = 50  # Variación aleatoria para evitar todos los workers reinicien a la vez
