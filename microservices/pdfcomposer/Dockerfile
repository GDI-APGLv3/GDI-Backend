FROM python:3.12-slim

# Crear un usuario y grupo no-root
RUN groupadd -r appgroup && useradd -r -g appgroup appuser

WORKDIR /app

# Instalar dependencias del sistema para WeasyPrint (Pango, Cairo, fontconfig)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpango-1.0-0 \
    libpangocairo-1.0-0 \
    libpangoft2-1.0-0 \
    libgdk-pixbuf-2.0-0 \
    libffi-dev \
    libcairo2 \
    libglib2.0-0 \
    fontconfig \
    fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

# Copiar requirements.txt primero para aprovechar la cache de Docker
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copiar el resto del codigo
COPY . .

# Registrar fuentes Montserrat en fontconfig
RUN mkdir -p /usr/local/share/fonts/montserrat && \
    cp app/fonts/*.ttf /usr/local/share/fonts/montserrat/ && \
    fc-cache -fv

# Cache de fontconfig writable para appuser
RUN mkdir -p /home/appuser/.cache/fontconfig && \
    chown -R appuser:appgroup /home/appuser

# Cambiar el propietario de los archivos
RUN chown -R appuser:appgroup /app

# Version del commit deployado (inyectado por GitHub Actions)
ARG GIT_SHA=unknown
ENV GIT_SHA=$GIT_SHA

# Cambiar al usuario no-root
USER appuser

# Exponer el puerto
EXPOSE 8080

# Comando para iniciar la aplicacion con Gunicorn usando archivo de configuracion
CMD ["gunicorn", "main:app", "-c", "gunicorn_conf.py"]
