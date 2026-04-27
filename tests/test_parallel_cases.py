"""
Test paralelo: crea N expedientes en paralelo y luego vincula 2 docs a cada uno.

Valida:
1. Creacion de expedientes en paralelo (incluye CAEX firma con Notary)
2. Vinculacion de docs (con la nueva validacion signed_at IS NOT NULL)
3. Paralelismo del lock 999999 (cases) post-refactor
4. Flujo inactive -> active del expediente

Uso:
    python tests/test_parallel_cases.py [N]

Default N=10. Apunta al backend en TESTING_MODE (configurable via GDI_TEST_BASE_URL).
"""

import asyncio
import sys
import time
from collections import Counter

import httpx

# === CONFIGURACION ===
import os
BASE_URL = os.getenv("GDI_TEST_BASE_URL", "http://localhost:8000")
USER_ID = "a1000000-0000-0000-0000-000000000100"
SCHEMA_NAME = "100_test"
CASE_TEMPLATE_ID = "c1000000-0000-0000-0000-000000000001"  # TEST template

HEADERS = {
    "X-User-ID": USER_ID,
    "X-Tenant-Schema": SCHEMA_NAME,
    "Content-Type": "application/json",
}

# Pool de docs ya firmados para vincular (de tests previos)
DOCS_TO_LINK = [
    "b59f59f4-13ee-45ef-b426-3f017ad93be2",
    "1f486233-d292-4218-bffa-bb078689af17",
    "df95a428-9be5-4a3c-9c1c-fa1dd6c17a0c",
    "164a0852-3523-4ede-8d8c-c58100f5639b",
    "7ce93fa5-94f2-4bb5-8587-9a466b55b790",
    "d84de908-3ad8-4a8a-b3ab-5faa7de52750",
    "065b2b9d-d56c-4587-b964-83b12ccd3b61",
    "2c654d1c-d4f2-48cf-b50c-81046647f5c9",
    "19693373-d2e6-44bb-bd5b-bf28c4b57ae8",
    "dd2405b9-2de6-43dd-b7c1-e843896ee094",
    "7f975303-54de-455b-a928-36a0fda65559",
    "ab7ca8eb-f137-485e-937d-4ea11331c0a1",
    "6eaafb00-68e3-4010-9281-46d7c8d5e2ad",
    "66b8838a-b7fa-4ab0-8a3e-0943baec3b14",
    "e1f58a4c-ad3b-48ed-b9de-28afd49f7204",
    "c3f7c592-0e0b-4483-897e-c4a039f5375d",
    "74a2d72b-2141-400e-8a4b-25b287f571d4",
    "71b9d4f0-b06b-4f15-9446-5d8491a57fd5",
    "24b4c3f9-93a6-4285-99d7-71c208e30fa6",
    "bdcf200e-83c7-47f3-9ce6-a53eb7177f14",
]


async def create_case(client, idx):
    start = time.monotonic()
    payload = {
        "case_template_id": CASE_TEMPLATE_ID,
        "reference": f"Test paralelo expediente #{idx} - {int(time.time())}",
    }
    try:
        r = await client.post(f"{BASE_URL}/api/v1/cases/", json=payload, headers=HEADERS, timeout=60.0)
        elapsed = time.monotonic() - start
        if r.status_code in (200, 201):
            data = r.json().get("data", {})
            return {
                "idx": idx,
                "success": True,
                "case_id": data.get("case", {}).get("case_id"),
                "case_number": data.get("case", {}).get("case_number"),
                "caex_number": data.get("cover", {}).get("official_number"),
                "elapsed": elapsed,
            }
        return {
            "idx": idx,
            "success": False,
            "status": r.status_code,
            "error": r.text[:200],
            "elapsed": elapsed,
        }
    except Exception as e:
        return {
            "idx": idx,
            "success": False,
            "status": "exception",
            "error": str(e)[:200],
            "elapsed": time.monotonic() - start,
        }


async def link_doc_to_case(client, case_id, doc_id, label):
    start = time.monotonic()
    payload = {"official_document_id": doc_id}
    try:
        r = await client.post(
            f"{BASE_URL}/api/v1/cases/{case_id}/documents/link",
            json=payload,
            headers=HEADERS,
            timeout=30.0,
        )
        elapsed = time.monotonic() - start
        return {
            "label": label,
            "case_id": case_id[:8],
            "doc_id": doc_id[:8],
            "success": r.status_code in (200, 201),
            "status": r.status_code,
            "elapsed": elapsed,
            "error": r.text[:150] if r.status_code not in (200, 201) else None,
        }
    except Exception as e:
        return {
            "label": label,
            "case_id": case_id[:8],
            "doc_id": doc_id[:8],
            "success": False,
            "status": "exception",
            "elapsed": time.monotonic() - start,
            "error": str(e)[:150],
        }


async def main(n=10):
    print(f"\n{'='*70}")
    print(f"TEST PARALELO: {n} expedientes + {n*2} vinculaciones")
    print(f"Backend: {BASE_URL}")
    print(f"{'='*70}\n")

    if len(DOCS_TO_LINK) < n * 2:
        print(f"[ERR] Necesito {n*2} docs para vincular, solo hay {len(DOCS_TO_LINK)}")
        return

    limits = httpx.Limits(max_connections=100, max_keepalive_connections=50)
    async with httpx.AsyncClient(limits=limits) as client:

        # ============================================
        # FASE 1: Crear N expedientes en PARALELO
        # ============================================
        print(f"FASE 1: Creando {n} expedientes EN PARALELO...")
        print("(estresa: lock 999999 cases + lock 888888 CAEX + Notary + R2)\n")

        t0 = time.monotonic()
        case_results = await asyncio.gather(
            *[create_case(client, i + 1) for i in range(n)]
        )
        case_elapsed = time.monotonic() - t0

        case_successes = [r for r in case_results if r["success"]]
        case_failures = [r for r in case_results if not r["success"]]
        case_times = [r["elapsed"] for r in case_results]

        print(f"\nFASE 1 RESULTADOS:")
        print(f"  Exitosos:        {len(case_successes)}/{n}")
        print(f"  Tiempo wall:     {case_elapsed:.2f}s")
        print(f"  Min/Max/Avg:     {min(case_times):.2f}s / {max(case_times):.2f}s / {sum(case_times)/len(case_times):.2f}s")

        if case_successes:
            case_numbers = [r["case_number"] for r in case_successes]
            caex_numbers = [r["caex_number"] for r in case_successes]
            print(f"\n  Case numbers:")
            for cn in sorted(set(case_numbers)):
                print(f"    {cn}")
            print(f"\n  CAEX numbers:")
            for cn in sorted(set(caex_numbers)):
                print(f"    {cn}")

            if len(case_numbers) != len(set(case_numbers)):
                print(f"\n  [ERR] CASE_NUMBER DUPLICADOS: {[n for n in case_numbers if case_numbers.count(n)>1]}")
            if len(caex_numbers) != len(set(caex_numbers)):
                print(f"\n  [ERR] CAEX DUPLICADOS")

        if case_failures:
            print(f"\n  Fallos:")
            for f in case_failures[:5]:
                print(f"    [{f['idx']}] {f['status']}: {f.get('error', '')[:120]}")

        if not case_successes:
            print("\n[ERR] No hay expedientes para vincular. Abortando.")
            return

        # ============================================
        # FASE 2: Vincular 2 docs a cada expediente EN PARALELO
        # ============================================
        print(f"\n{'='*70}")
        print(f"FASE 2: Vinculando 2 docs a cada uno de los {len(case_successes)} expedientes")
        print(f"        (total: {len(case_successes)*2} vinculaciones EN PARALELO)")
        print(f"{'='*70}\n")

        link_tasks = []
        doc_index = 0
        for case in case_successes:
            for slot in range(2):
                doc_id = DOCS_TO_LINK[doc_index % len(DOCS_TO_LINK)]
                doc_index += 1
                label = f"case#{case['idx']}/doc{slot+1}"
                link_tasks.append(link_doc_to_case(client, case["case_id"], doc_id, label))

        t0 = time.monotonic()
        link_results = await asyncio.gather(*link_tasks)
        link_elapsed = time.monotonic() - t0

        link_successes = [r for r in link_results if r["success"]]
        link_failures = [r for r in link_results if not r["success"]]
        link_times = [r["elapsed"] for r in link_results]

        print(f"FASE 2 RESULTADOS:")
        print(f"  Vinculaciones exitosas: {len(link_successes)}/{len(link_results)}")
        print(f"  Tiempo wall:            {link_elapsed:.2f}s")
        print(f"  Min/Max/Avg:            {min(link_times):.2f}s / {max(link_times):.2f}s / {sum(link_times)/len(link_times):.2f}s")

        if link_failures:
            print(f"\n  Fallos:")
            status_counts = Counter(str(f["status"]) for f in link_failures)
            for s, c in status_counts.items():
                print(f"    {s}: {c}")
            print(f"\n  Detalle (primeros 5):")
            for f in link_failures[:5]:
                print(f"    [{f['label']}] {f['status']}: {f.get('error', '')[:120]}")

        # ============================================
        # RESUMEN
        # ============================================
        print(f"\n{'='*70}")
        print("RESUMEN GENERAL")
        print(f"{'='*70}")
        print(f"  Expedientes creados:     {len(case_successes)}/{n}")
        print(f"  Vinculaciones exitosas:  {len(link_successes)}/{len(link_results)}")
        print(f"  Tiempo total wall:       {case_elapsed + link_elapsed:.2f}s")
        print(f"  Tiempo creacion (paralelo): {case_elapsed:.2f}s")
        print(f"  Tiempo vinculacion (paralelo): {link_elapsed:.2f}s")
        print(f"{'='*70}\n")


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 10
    asyncio.run(main(n))
