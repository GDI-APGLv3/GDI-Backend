"""
Script de prueba para verificar el fix de IDOR en unified_details.

Este script prueba que:
1. El creador puede ver su documento
2. Un firmante puede ver el documento
3. Un usuario con permiso can_view en el sector puede ver el documento
4. Un usuario sin permisos NO puede ver el documento (403)
"""

import asyncio
import sys
from uuid import uuid4

# Agregar path para imports
sys.path.insert(0, 'C:/Users/santi/OneDrive/Desktop/GDILatam/MVP-GDILatam/GDI-Backend')

from services.documents.retrieval.unified_details import get_unified_document_details
from shared.exceptions import AuthorizationError, DocumentNotFoundError

# BD de pruebas
TEST_SCHEMA = "100_test"


async def test_document_permissions():
    """Test de permisos de documentos."""

    print("=" * 80)
    print("TEST: Verificación de permisos en unified_details")
    print("=" * 80)

    # Documento de prueba (debes reemplazar con un ID real de 100_test)
    # Este documento debe existir en BD de pruebas
    test_document_id = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"  # REEMPLAZAR

    # Usuarios de prueba (debes reemplazar con IDs reales)
    creator_user_id = "creator-uuid-here"  # Usuario creador
    signer_user_id = "signer-uuid-here"    # Usuario firmante
    authorized_user_id = "authorized-uuid-here"  # Usuario con can_view
    unauthorized_user_id = str(uuid4())    # Usuario sin permisos

    print("\n1. TEST: Creador puede ver el documento")
    print("-" * 80)
    try:
        result = await get_unified_document_details(
            test_document_id,
            creator_user_id,
            schema_name=TEST_SCHEMA
        )
        print(f"✅ PASS - Creador puede ver el documento")
        print(f"   Document ID: {result['document_id']}")
        print(f"   Status: {result['status']}")
    except AuthorizationError as e:
        print(f"❌ FAIL - Creador NO puede ver su propio documento: {e}")
    except DocumentNotFoundError:
        print(f"⚠️  SKIP - Documento de prueba no existe. Actualiza test_document_id")
    except Exception as e:
        print(f"❌ ERROR - {type(e).__name__}: {e}")

    print("\n2. TEST: Firmante puede ver el documento")
    print("-" * 80)
    try:
        result = await get_unified_document_details(
            test_document_id,
            signer_user_id,
            schema_name=TEST_SCHEMA
        )
        print(f"✅ PASS - Firmante puede ver el documento")
        print(f"   Document ID: {result['document_id']}")
    except AuthorizationError as e:
        print(f"❌ FAIL - Firmante NO puede ver el documento: {e}")
    except DocumentNotFoundError:
        print(f"⚠️  SKIP - Documento de prueba no existe")
    except Exception as e:
        print(f"❌ ERROR - {type(e).__name__}: {e}")

    print("\n3. TEST: Usuario con can_view puede ver el documento")
    print("-" * 80)
    try:
        result = await get_unified_document_details(
            test_document_id,
            authorized_user_id,
            schema_name=TEST_SCHEMA
        )
        print(f"✅ PASS - Usuario autorizado puede ver el documento")
        print(f"   Document ID: {result['document_id']}")
    except AuthorizationError as e:
        print(f"❌ FAIL - Usuario autorizado NO puede ver el documento: {e}")
    except DocumentNotFoundError:
        print(f"⚠️  SKIP - Documento de prueba no existe")
    except Exception as e:
        print(f"❌ ERROR - {type(e).__name__}: {e}")

    print("\n4. TEST: Usuario sin permisos NO puede ver el documento (debe fallar con 403)")
    print("-" * 80)
    try:
        result = await get_unified_document_details(
            test_document_id,
            unauthorized_user_id,
            schema_name=TEST_SCHEMA
        )
        print(f"❌ FAIL - Usuario sin permisos PUDO ver el documento (IDOR no resuelto)")
        print(f"   Document ID: {result['document_id']}")
    except AuthorizationError as e:
        print(f"✅ PASS - Usuario sin permisos fue rechazado correctamente (403)")
        print(f"   Error: {e}")
    except DocumentNotFoundError:
        print(f"⚠️  SKIP - Documento de prueba no existe")
    except Exception as e:
        print(f"❌ ERROR - {type(e).__name__}: {e}")

    print("\n" + "=" * 80)
    print("FIN DE TESTS")
    print("=" * 80)
    print("\nPara ejecutar tests reales:")
    print("1. Actualiza test_document_id con un documento real de 100_test")
    print("2. Actualiza creator_user_id, signer_user_id, authorized_user_id con usuarios reales")
    print("3. Re-ejecuta el script")


if __name__ == "__main__":
    asyncio.run(test_document_permissions())
