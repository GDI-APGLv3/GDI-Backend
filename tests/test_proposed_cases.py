"""
Script de prueba para la funcionalidad de proposed_cases.
Verifica que la deduplicación funcione correctamente.
"""

from services.documents.lifecycle.editing import _build_proposed_cases_update_operations


def test_deduplication():
    """Test: Deduplicación de case_ids"""
    print("\n=== TEST: Deduplicación ===")

    # Caso 1: Lista con duplicados
    document_id = "550e8400-e29b-41d4-a716-446655440000"
    user_id = "550e8400-e29b-41d4-a716-446655440001"
    proposed_case_ids = [
        "550e8400-e29b-41d4-a716-446655440010",
        "550e8400-e29b-41d4-a716-446655440011",
        "550e8400-e29b-41d4-a716-446655440010",  # Duplicado
    ]

    operations = _build_proposed_cases_update_operations(
        document_id, proposed_case_ids, user_id
    )

    # Debe tener: 1 DELETE + 2 INSERT (deduplicados)
    assert len(operations) == 3, f"Esperaba 3 operaciones, obtuvo {len(operations)}"
    print(f"OK: {len(proposed_case_ids)} case_ids -> {len(operations) - 1} operaciones INSERT")

    # Caso 2: Lista vacía
    operations_empty = _build_proposed_cases_update_operations(
        document_id, [], user_id
    )
    assert len(operations_empty) == 1, "Esperaba solo 1 DELETE para lista vacía"
    print("OK: Lista vacía genera solo DELETE")

    # Caso 3: Sin duplicados
    operations_unique = _build_proposed_cases_update_operations(
        document_id,
        ["550e8400-e29b-41d4-a716-446655440010"],
        user_id
    )
    assert len(operations_unique) == 2, "Esperaba 1 DELETE + 1 INSERT"
    print("OK: Sin duplicados funciona correctamente")

    print("\nTEST PASSED: Deduplicación funciona correctamente")


if __name__ == "__main__":
    test_deduplication()
    print("\n=== TODAS LAS PRUEBAS PASARON ===")
