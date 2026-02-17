"""
Servidor de producción para Railway
Este archivo inicia uvicorn con la configuración correcta para Railway
"""
import os
import uvicorn

if __name__ == "__main__":
    # Detectar si estamos en Railway (PORT está seteado)
    is_production = os.getenv("PORT") is not None

    # Configuración basada en entorno
    host = "0.0.0.0" if is_production else "127.0.0.1"
    port = int(os.getenv("PORT", 8000))
    reload = not is_production  # Reload solo en desarrollo

    print(f"[SERVER] Iniciando en {'PRODUCCIÓN' if is_production else 'DESARROLLO'}")
    print(f"[SERVER] Host: {host}, Port: {port}, Reload: {reload}")

    uvicorn.run(
        "main:app",
        host=host,
        port=port,
        reload=reload,
        access_log=True,
        log_level="info"
    )
