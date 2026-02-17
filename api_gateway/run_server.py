#!/usr/bin/env python3
"""
Script de arranque para el MCP Server de GDI-Backend.
Configura el path correctamente antes de ejecutar el server.
"""
import sys
import os
from pathlib import Path

# Agregar el directorio raíz del backend al path
backend_root = Path(__file__).parent.parent
sys.path.insert(0, str(backend_root))

# Cargar variables de entorno desde .env
from dotenv import load_dotenv
load_dotenv(backend_root / ".env")

# Verificar que MCP_API_KEY esté configurada
if not os.getenv("MCP_API_KEY"):
    print("ERROR: MCP_API_KEY no está configurada en variables de entorno", file=sys.stderr)
    print("Agrega 'MCP_API_KEY=tu-api-key-secreta' a tu archivo .env", file=sys.stderr)
    sys.exit(1)

# Importar y ejecutar el server
from api_gateway.server import main
import asyncio

if __name__ == "__main__":
    # IMPORTANTE: Los prints van a stderr porque stdout es usado por MCP JSON-RPC
    import sys
    print("=" * 60, file=sys.stderr)
    print("GDI-Backend MCP Server", file=sys.stderr)
    print("=" * 60, file=sys.stderr)
    print(f"Backend root: {backend_root}", file=sys.stderr)
    print(f"API Key configurada: {'Yes' if os.getenv('MCP_API_KEY') else 'No'}", file=sys.stderr)
    print(f"Database: {os.getenv('DB_HOST', 'not configured')}", file=sys.stderr)
    print("=" * 60, file=sys.stderr)

    asyncio.run(main())
