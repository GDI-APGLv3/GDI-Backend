FROM python:3.12-slim AS builder
WORKDIR /app
RUN apt-get update && apt-get install -y gcc libpq-dev && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

FROM python:3.12-slim
WORKDIR /app
RUN apt-get update && apt-get install -y libpq5 curl && rm -rf /var/lib/apt/lists/*
RUN groupadd -r app && useradd -r -g app -m -d /home/app app
COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin
COPY . .
RUN chown -R app:app /app
USER app
ARG GIT_SHA=unknown
ENV GIT_SHA=$GIT_SHA
EXPOSE 8080
# Workers configurable por env (default 2). 'exec' preserva el manejo de señales (SIGTERM de Fly).
CMD exec gunicorn main:app -k uvicorn.workers.UvicornWorker --bind "[::]:8080" --timeout 120 --workers ${GUNICORN_WORKERS:-2}
