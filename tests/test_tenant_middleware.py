"""
Script de verificacion del middleware multi-tenant.
Verifica que todos los componentes esten correctamente importados.
"""

import sys

def test_imports():
    """Verifica que todos los modulos se importen correctamente"""
    print("=== Test de Imports del Sistema Multi-Tenant ===\n")

    try:
        print("1. Importando modelos de tenant...")
        from models.tenant_models import (
            TenantAccess,
            OnboardingUser,
            UserProfile,
            OnboardingResponse
        )
        print("   [OK] Modelos importados correctamente\n")

        print("2. Importando funciones de validacion de tenant...")
        from shared.tenant_validation import (
            get_user_tenants,
            validate_tenant_access,
            invalidate_user_cache,
            get_valid_schemas,
            is_valid_schema
        )
        print("   [OK] Funciones de validacion importadas correctamente\n")

        print("3. Importando middleware...")
        from middleware.tenant_middleware import TenantMiddleware
        print("   [OK] Middleware importado correctamente\n")

        print("4. Importando funcion de auth...")
        from auth import decode_jwt_from_request
        print("   [OK] Funcion decode_jwt_from_request importada correctamente\n")

        print("5. Verificando database.py...")
        from database import get_db_cursor
        import inspect
        sig = inspect.signature(get_db_cursor)
        params = list(sig.parameters.keys())
        if 'schema_name' in params:
            print("   [OK] database.get_db_cursor tiene parametro schema_name\n")
        else:
            print("   [FAIL] database.get_db_cursor NO tiene parametro schema_name\n")
            return False

        print("=== [OK] Todos los tests pasaron correctamente ===")
        return True

    except ImportError as e:
        print(f"\n[FAIL] Error de import: {e}")
        return False
    except Exception as e:
        print(f"\n[FAIL] Error inesperado: {e}")
        return False


def test_middleware_paths():
    """Verifica las rutas excluidas del middleware"""
    print("\n=== Test de Rutas Excluidas ===\n")

    try:
        from middleware.tenant_middleware import TenantMiddleware

        excluded = TenantMiddleware.EXCLUDED_PATHS
        print(f"Rutas excluidas: {len(excluded)}")
        for path in sorted(excluded):
            print(f"  - {path}")

        # Verificar que /api/auth/onboarding este excluida
        if "/api/auth/onboarding" in excluded:
            print("\n[OK] /api/auth/onboarding esta excluida (correcto)")
        else:
            print("\n[FAIL] /api/auth/onboarding NO esta excluida (ERROR)")
            return False

        return True

    except Exception as e:
        print(f"\n[FAIL] Error: {e}")
        return False


def test_cache_functions():
    """Verifica las funciones de cache"""
    print("\n=== Test de Funciones de Cache ===\n")

    try:
        from shared.tenant_validation import (
            get_user_tenants,
            invalidate_user_cache,
            CACHE_TTL_MINUTES
        )

        print(f"Cache TTL: {CACHE_TTL_MINUTES} minutos")

        # Test básico de get_user_tenants (debería retornar public hardcoded)
        print("\nTest get_user_tenants('test@example.com')...")
        tenants = get_user_tenants("test@example.com")
        print(f"  Tenants: {tenants}")

        if tenants and len(tenants) > 0:
            print("  ✓ Retorna tenants (hardcoded a 'public')")
        else:
            print("  ✗ No retorna tenants")
            return False

        # Test invalidación de cache
        print("\nTest invalidate_user_cache('test@example.com')...")
        invalidate_user_cache("test@example.com")
        print("  ✓ Cache invalidado sin errores")

        return True

    except Exception as e:
        print(f"\n✗ Error: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_valid_schemas():
    """Verifica la función de schemas válidos"""
    print("\n=== Test de Schemas Válidos ===\n")

    try:
        from shared.tenant_validation import get_valid_schemas, is_valid_schema

        print("Obteniendo schemas válidos...")
        schemas = get_valid_schemas()
        print(f"  Schemas encontrados: {schemas}")

        # Verificar que 'public' esté incluido
        if "public" in schemas:
            print("  ✓ 'public' está en la lista de schemas válidos")
        else:
            print("  ✗ 'public' NO está en la lista")
            return False

        # Test de is_valid_schema
        print("\nTest is_valid_schema('public')...")
        if is_valid_schema("public"):
            print("  ✓ 'public' es válido")
        else:
            print("  ✗ 'public' NO es válido")
            return False

        print("\nTest is_valid_schema('invalid_schema')...")
        if not is_valid_schema("invalid_schema"):
            print("  ✓ 'invalid_schema' es inválido (correcto)")
        else:
            print("  ✗ 'invalid_schema' es válido (ERROR)")
            return False

        return True

    except Exception as e:
        print(f"\n✗ Error: {e}")
        import traceback
        traceback.print_exc()
        return False


def main():
    """Ejecuta todos los tests"""
    print("\n" + "="*60)
    print(" VERIFICACIÓN DEL MIDDLEWARE MULTI-TENANT")
    print("="*60 + "\n")

    results = []

    # Test 1: Imports
    results.append(("Imports", test_imports()))

    # Test 2: Rutas excluidas
    results.append(("Rutas Excluidas", test_middleware_paths()))

    # Test 3: Funciones de cache
    results.append(("Cache", test_cache_functions()))

    # Test 4: Schemas válidos
    results.append(("Schemas Válidos", test_valid_schemas()))

    # Resumen
    print("\n" + "="*60)
    print(" RESUMEN")
    print("="*60 + "\n")

    total = len(results)
    passed = sum(1 for _, result in results if result)

    for name, result in results:
        status = "✓ PASS" if result else "✗ FAIL"
        print(f"{status}: {name}")

    print(f"\nResultado: {passed}/{total} tests pasaron")

    if passed == total:
        print("\n🎉 ¡Todos los tests pasaron! El middleware está correctamente instalado.\n")
        return 0
    else:
        print(f"\n⚠️  {total - passed} test(s) fallaron. Revisar errores arriba.\n")
        return 1


if __name__ == "__main__":
    sys.exit(main())
