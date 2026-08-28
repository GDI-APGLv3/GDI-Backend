import os
import multiprocessing
import logging

port = os.environ["PORT"]
bind = f"[::]:{port}"

workers = int(os.environ["GUNICORN_WORKERS"])

worker_class = "uvicorn.workers.UvicornWorker"

timeout = int(os.environ["GUNICORN_TIMEOUT"])

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

def pre_exec(server):
    server.log.info("Forked master process, preparing for exec")

def when_ready(server):
    server.log.info(f"Server listo. Escuchando en {bind}")

def worker_exit(server, worker):
    server.log.info(f"Worker exited (pid: {worker.pid})")

proc_name = "notary"

preload_app = False

graceful_timeout = 30

max_requests = int(os.getenv("GUNICORN_MAX_REQUESTS", "1000"))
max_requests_jitter = 50
