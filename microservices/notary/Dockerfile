FROM python:3.12-slim

# Instalar dependencias del sistema
RUN apt-get update && apt-get install -y \
    wget \
    unzip \
    fontconfig \
    && rm -rf /var/lib/apt/lists/*

# Crear directorio de trabajo
WORKDIR /app

# Crear directorios necesarios
RUN mkdir -p /app/certs /app/fonts

# Copiar requirements y instalar dependencias Python
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Descargar fuente Montserrat de forma robusta (opcional para evitar fallos de despliegue)
RUN wget -O /tmp/montserrat.zip "https://fonts.google.com/download?family=Montserrat" 2>/dev/null || \
    echo "No se pudo descargar Montserrat, usando fuentes del sistema" && \
    if [ -f /tmp/montserrat.zip ]; then \
        unzip /tmp/montserrat.zip -d /app/fonts/ 2>/dev/null || echo "Error al descomprimir fuentes"; \
        rm -f /tmp/montserrat.zip; \
    fi && \
    fc-cache -f -v 2>/dev/null || echo "Font cache actualizado"

# Copiar código de la aplicación
COPY app/ ./app/

# Copiar configuración de Gunicorn
COPY gunicorn_conf.py .

# Copiar certificados de prueba
COPY certs/ ./certs/

# Copiar fuentes
COPY fonts/ ./fonts/

# Variables de entorno por defecto (NO sensibles)
ENV TZ=UTC

# Exponer puerto
EXPOSE 8080

# Comando de inicio con Gunicorn
CMD ["gunicorn", "app.main:app", "-c", "gunicorn_conf.py"]
