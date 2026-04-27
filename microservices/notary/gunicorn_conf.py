"""
Configuración de Gunicorn para Notary API

Este archivo define la configuración del servidor Gunicorn para producción,
optimizado para Fly.io con soporte IPv6 y múltiples workers.
"""
import os
import multiprocessing
import logging

# Bind a todas las interfaces (IPv4 + IPv6) en el puerto proporcionado
port = os.environ["PORT"]
bind = f"[::]:{port}"

# Número de workers — fuente de verdad en fly.toml / fly.prd.toml
workers = int(os.environ["GUNICORN_WORKERS"])

# Clase de worker (usar UvicornWorker para compatibilidad con FastAPI async)
worker_class = "uvicorn.workers.UvicornWorker"

# Timeout para requests largos (firma de PDFs puede tomar tiempo) — fuente de verdad en fly.toml
timeout = int(os.environ["GUNICORN_TIMEOUT"])

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
proc_name = "notary"

# Preload app para mejorar tiempo de inicio
preload_app = False  # False para evitar problemas con recursos compartidos

# Graceful timeout para shutdowns
graceful_timeout = 30

# Max requests por worker (para prevenir memory leaks)
max_requests = int(os.getenv("GUNICORN_MAX_REQUESTS", "1000"))
max_requests_jitter = 50  # Variación aleatoria para evitar todos los workers reinicien a la vez
