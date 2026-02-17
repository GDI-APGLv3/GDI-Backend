#!/usr/bin/env python3
"""
Script de validación rápida para verificar que todos los imports funcionan.
NO ejecuta el server, solo verifica que no hay errores de importación.
"""
import sys
from pathlib import Path

# Agregar el directorio raíz del backend al path
backend_root = Path(__file__).parent.parent
sys.path.insert(0, str(backend_root))

print("=" * 60)
print("API Gateway - Validación de Imports")
print("=" * 60)

errors = []

# Test 1: Imports básicos
print("\n[1/6] Verificando imports básicos...")
try:
    import api_gateway.auth
    import api_gateway.context
    print("[OK] api_gateway.auth y api_gateway.context OK")
except Exception as e:
    errors.append(f"Error en imports básicos: {e}")
    print(f"[FAIL] Error: {e}")

# Test 2: Tools
print("\n[2/6] Verificando tools...")
try:
    import api_gateway.tools.cases
    import api_gateway.tools.documents
    import api_gateway.tools.system
    print("[OK] api_gateway.tools.* OK")
except Exception as e:
    errors.append(f"Error en tools: {e}")
    print(f"[FAIL] Error: {e}")

# Test 3: Services reutilizados
print("\n[3/6] Verificando services reutilizados...")
try:
    from services.case_service import CaseService
    from services.users.user_documents import get_user_documents
    from services.documents.editing import get_document_details_for_editing
    from services.documents.catalog.types import get_all_document_types
    from services.sector_service import SectorService
    print("[OK] Services reutilizados OK")
except Exception as e:
    errors.append(f"Error en services: {e}")
    print(f"[FAIL] Error: {e}")

# Test 4: Database
print("\n[4/6] Verificando database...")
try:
    from database import execute_query
    print("[OK] database.execute_query OK")
except Exception as e:
    errors.append(f"Error en database: {e}")
    print(f"[FAIL] Error: {e}")

# Test 5: MCP SDK
print("\n[5/6] Verificando MCP SDK...")
try:
    from mcp.server import Server
    from mcp.server.stdio import stdio_server
    from mcp.types import Tool, TextContent, CallToolResult
    print("[OK] MCP SDK OK")
except Exception as e:
    errors.append(f"Error en MCP SDK: {e}")
    print(f"[FAIL] Error: {e}")
    print("  Instalar con: pip install mcp>=1.1.0")

# Test 6: Server principal
print("\n[6/6] Verificando server principal...")
try:
    import api_gateway.server
    print("[OK] api_gateway.server OK")
except Exception as e:
    errors.append(f"Error en server: {e}")
    print(f"[FAIL] Error: {e}")

# Resumen
print("\n" + "=" * 60)
if errors:
    print(f"FALLÓ: {len(errors)} errores encontrados")
    print("=" * 60)
    for i, error in enumerate(errors, 1):
        print(f"{i}. {error}")
    sys.exit(1)
else:
    print("ÉXITO: Todos los imports funcionan correctamente")
    print("=" * 60)
    print("\nPróximo paso: Configurar MCP_API_KEY en .env")
    print("Luego ejecutar: python api_gateway/run_server.py")
    sys.exit(0)
