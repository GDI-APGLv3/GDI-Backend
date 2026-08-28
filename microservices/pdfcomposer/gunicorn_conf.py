import os
import multiprocessing
import logging

port = os.getenv("PORT", "8000")
bind = f"[::]:{port}"

workers = int(os.getenv("GUNICORN_WORKERS", "4"))

worker_class = "uvicorn.workers.UvicornWorker"

timeout = int(os.getenv("GUNICORN_TIMEOUT", "120"))

keepalive = 5

accesslog = "-"
errorlog = "-"
loglevel = os.getenv("LOG_LEVEL", "info")

def on_starting(server):
    server.log.info(f"Iniciando Gunicorn con {workers} workers en {bind}")

def on_reload(server):
    server.log.info("Recargando configuración de Gunicorn")

def worker_int(worker):
    worker.log.info(f"Worker {worker.pid} interrumpido")

def worker_abort(worker):
    worker.log.info(f"Worker {worker.pid} abortado")

def pre_fork(server, worker):
    pass

def post_fork(server, worker):
    server.log.info(f"Worker spawned (pid: {worker.pid})")

    if os.getenv("BETTERSTACK_SOURCE_TOKEN"):
        try:
            from logtail import LogtailHandler
            handler = LogtailHandler(source_token=os.getenv("BETTERSTACK_SOURCE_TOKEN"))

            root_logger = logging.getLogger()
            root_logger.setLevel(logging.INFO)
            root_logger.addHandler(handler)

            gunicorn_logger = logging.getLogger("gunicorn.access")
            gunicorn_logger.addHandler(handler)
            gunicorn_error_logger = logging.getLogger("gunicorn.error")
            gunicorn_error_logger.addHandler(handler)

            server.log.info(f"Better Stack logging configured for worker {worker.pid}")
        except Exception as e:
            server.log.error(f"Failed to configure Better Stack logging: {e}")

def pre_exec(server):
    server.log.info("Forked master process, preparing for exec")

def when_ready(server):
    server.log.info(f"Server listo. Escuchando en {bind}")

def worker_exit(server, worker):
    server.log.info(f"Worker exited (pid: {worker.pid})")

proc_name = "pdfcomposer"

preload_app = False

graceful_timeout = 30

max_requests = int(os.getenv("GUNICORN_MAX_REQUESTS", "300"))
max_requests_jitter = 50
