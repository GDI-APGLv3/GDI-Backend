"""
Script de verificacion del middleware multi-tenant (version simple ASCII).
"""

import sys

print("\n" + "="*60)
print(" VERIFICACION DEL MIDDLEWARE MULTI-TENANT")
print("="*60 + "\n")

# Test 1: Imports
print("Test 1: Verificando imports...")
try:
    from models.tenant_models import TenantAccess, OnboardingUser, UserProfile, OnboardingResponse
    from shared.tenant_validation import get_user_tenants, validate_tenant_access, is_valid_schema
    from middleware.tenant_middleware import TenantMiddleware
    from auth import decode_jwt_from_request
    from database import get_db_cursor
    import inspect

    # Verificar que get_db_cursor tenga parametro schema_name
    sig = inspect.signature(get_db_cursor)
    params = list(sig.parameters.keys())

    if 'schema_name' in params:
        print("[OK] Todos los imports exitosos")
        print("[OK] database.get_db_cursor tiene parametro schema_name")
    else:
        print("[FAIL] database.get_db_cursor NO tiene parametro schema_name")
        sys.exit(1)

except ImportError as e:
    print(f"[FAIL] Error de import: {e}")
    sys.exit(1)

# Test 2: Rutas excluidas
print("\nTest 2: Verificando rutas excluidas...")
excluded = TenantMiddleware.EXCLUDED_PATHS
print(f"Rutas excluidas: {sorted(excluded)}")

if "/api/auth/onboarding" in excluded:
    print("[OK] /api/auth/onboarding esta excluida")
else:
    print("[FAIL] /api/auth/onboarding NO esta excluida")
    sys.exit(1)

# Test 3: Cache
print("\nTest 3: Verificando cache...")
try:
    from shared.tenant_validation import CACHE_TTL_MINUTES, invalidate_user_cache
    print(f"Cache TTL: {CACHE_TTL_MINUTES} minutos")

    tenants = get_user_tenants("test@example.com")
    print(f"Tenants retornados: {tenants}")

    if tenants and len(tenants) > 0:
        print("[OK] get_user_tenants funciona correctamente")
    else:
        print("[FAIL] get_user_tenants no retorna datos")
        sys.exit(1)

    invalidate_user_cache("test@example.com")
    print("[OK] invalidate_user_cache funciona correctamente")

except Exception as e:
    print(f"[FAIL] Error en cache: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

# Test 4: Schemas validos
print("\nTest 4: Verificando schemas validos...")
try:
    schemas = get_valid_schemas()
    print(f"Schemas encontrados: {schemas}")

    if "public" in schemas:
        print("[OK] 'public' esta en schemas validos")
    else:
        print("[FAIL] 'public' NO esta en schemas validos")
        sys.exit(1)

    if is_valid_schema("public"):
        print("[OK] is_valid_schema('public') = True")
    else:
        print("[FAIL] is_valid_schema('public') = False")
        sys.exit(1)

    if not is_valid_schema("invalid_schema"):
        print("[OK] is_valid_schema('invalid_schema') = False")
    else:
        print("[FAIL] is_valid_schema('invalid_schema') = True")
        sys.exit(1)

except Exception as e:
    print(f"[FAIL] Error validando schemas: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

# Resumen
print("\n" + "="*60)
print(" RESUMEN")
print("="*60)
print("\n[OK] Todos los tests pasaron!")
print("El middleware multi-tenant esta correctamente instalado.\n")
sys.exit(0)
