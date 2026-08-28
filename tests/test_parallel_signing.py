
import asyncio
import sys
import time
from collections import Counter

import httpx

BASE_URL = "https://<your-backend-app>.fly.dev"
USER_ID = "a1000000-0000-0000-0000-000000000100"
SCHEMA_NAME = "100_test"
DOCUMENT_TYPE_ACRONYM = "ANEXO"

HEADERS = {
    "X-User-ID": USER_ID,
    "X-Tenant-Schema": SCHEMA_NAME,
    "Content-Type": "application/json",
}


HTML_CONTENT = """
<p>Documento de prueba para test de firma en paralelo.</p>
<p>Generado automaticamente.</p>
<br/><br/>
<p>Linea 1 - Lorem ipsum dolor sit amet, consectetur adipiscing elit.</p>
<p>Linea 2 - Sed do eiusmod tempor incididunt ut labore et dolore magna aliqua.</p>
<p>Linea 3 - Ut enim ad minim veniam, quis nostrud exercitation ullamco laboris.</p>
<p>Linea 4 - Nisi ut aliquip ex ea commodo consequat.</p>
<p>Linea 5 - Duis aute irure dolor in reprehenderit in voluptate velit esse.</p>
<p>Linea 6 - Cillum dolore eu fugiat nulla pariatur.</p>
<p>Linea 7 - Excepteur sint occaecat cupidatat non proident, sunt in culpa.</p>
<p>Linea 8 - Qui officia deserunt mollit anim id est laborum.</p>
<br/><br/><br/>
"""


async def create_document(client: httpx.AsyncClient, idx: int) -> dict:
    payload = {
        "document_type_acronym": DOCUMENT_TYPE_ACRONYM,
        "reference": f"TEST PARALELO #{idx} - {int(time.time())}",
    }
    r = await client.post(f"{BASE_URL}/documents", json=payload, headers=HEADERS, timeout=30.0)
    r.raise_for_status()
    return r.json()


async def save_document(client: httpx.AsyncClient, document_id: str) -> dict:
    payload = {
        "content": HTML_CONTENT,
        "signers": [
            {
                "user_id": USER_ID,
                "is_numerator": True,
                "signing_order": 1,
            }
        ],
    }
    r = await client.patch(
        f"{BASE_URL}/documents/{document_id}/save",
        json=payload,
        headers=HEADERS,
        timeout=30.0,
    )
    r.raise_for_status()
    return r.json()


async def start_signing(client: httpx.AsyncClient, document_id: str) -> dict:
    r = await client.post(
        f"{BASE_URL}/documents/{document_id}/start-signing-process",
        headers=HEADERS,
        timeout=60.0,
    )
    r.raise_for_status()
    return r.json()


async def super_sign(client: httpx.AsyncClient, document_id: str) -> tuple:
    start = time.monotonic()
    try:
        r = await client.post(
            f"{BASE_URL}/documents/{document_id}/super-sign",
            headers=HEADERS,
            timeout=60.0,
        )
        elapsed = time.monotonic() - start
        if r.status_code == 200:
            data = r.json()
            return {
                "success": True,
                "document_id": document_id,
                "official_number": data.get("official_number"),
                "elapsed": elapsed,
                "status": r.status_code,
            }
        else:
            return {
                "success": False,
                "document_id": document_id,
                "elapsed": elapsed,
                "status": r.status_code,
                "error": r.text[:200],
            }
    except Exception as e:
        return {
            "success": False,
            "document_id": document_id,
            "elapsed": time.monotonic() - start,
            "status": "exception",
            "error": str(e)[:200],
        }


async def prepare_document(client: httpx.AsyncClient, idx: int) -> str | None:
    try:
        created = await create_document(client, idx)
        document_id = created.get("document_id") or created.get("id")
        if not document_id:
            print(f"  [{idx:2d}] [ERR] create_document sin id: {created}")
            return None
        await save_document(client, document_id)
        await start_signing(client, document_id)
        print(f"  [{idx:2d}] [OK] Doc preparado: {document_id[:8]}")
        return document_id
    except httpx.HTTPStatusError as e:
        print(f"  [{idx:2d}] [ERR] {e.response.status_code} en {e.request.url.path}: {e.response.text[:150]}")
        return None
    except Exception as e:
        print(f"  [{idx:2d}] [ERR] {type(e).__name__}: {str(e)[:150]}")
        return None


async def main(n: int = 10):
    print(f"\n{'='*70}")
    print(f"TEST DE FIRMA PARALELA - {n} documentos en {SCHEMA_NAME}")
    print(f"Backend: {BASE_URL}")
    print(f"{'='*70}\n")

    limits = httpx.Limits(max_connections=100, max_keepalive_connections=50)
    async with httpx.AsyncClient(limits=limits) as client:
        print(f"FASE 1: Preparando {n} documentos en sent_to_sign EN PARALELO...")
        print("(create + save + start_signing concurrente - estresa PDFComposer)\n")
        t0 = time.monotonic()
        prep_results = await asyncio.gather(
            *[prepare_document(client, i + 1) for i in range(n)]
        )
        document_ids = [doc_id for doc_id in prep_results if doc_id]
        prep_elapsed = time.monotonic() - t0
        print(f"\nPreparados: {len(document_ids)}/{n} en {prep_elapsed:.1f}s wall (paralelo)\n")

        if not document_ids:
            print("[ERR] No hay documentos para firmar. Abortando.")
            return

        print(f"FASE 2: Firmando {len(document_ids)} documentos EN PARALELO...")
        print("(esto es lo importante: medir si el lock se serializa o no)\n")

        t0 = time.monotonic()
        results = await asyncio.gather(
            *[super_sign(client, doc_id) for doc_id in document_ids]
        )
        total_elapsed = time.monotonic() - t0

        successes = [r for r in results if r["success"]]
        failures = [r for r in results if not r["success"]]
        times = [r["elapsed"] for r in results]

        print(f"\n{'='*70}")
        print("RESULTADOS")
        print(f"{'='*70}")
        print(f"Total firmas:       {len(results)}")
        print(f"Exitosas:           {len(successes)} ({len(successes)*100//len(results)}%)")
        print(f"Fallidas:           {len(failures)}")
        print(f"")
        print(f"Tiempo total wall:  {total_elapsed:.2f}s (todo en paralelo)")
        print(f"Tiempo min/firma:   {min(times):.2f}s")
        print(f"Tiempo max/firma:   {max(times):.2f}s")
        print(f"Tiempo promedio:    {sum(times)/len(times):.2f}s")

        if successes:
            numbers = [r["official_number"] for r in successes if r.get("official_number")]
            print(f"")
            print(f"Numeros oficiales asignados:")
            for n in sorted(set(numbers)):
                print(f"  {n}")
            if len(numbers) != len(set(numbers)):
                print(f"\n*** [ERR] ERROR CRITICO: NUMEROS DUPLICADOS ***")
                duplicates = [n for n in numbers if numbers.count(n) > 1]
                print(f"Duplicados: {set(duplicates)}")
            else:
                print(f"\n[OK] {len(numbers)} numeros UNICOS")

        if failures:
            print(f"\nFALLOS:")
            status_counts = Counter(str(f["status"]) for f in failures)
            for status, count in status_counts.most_common():
                print(f"  {status}: {count}")
            print(f"\nDetalle de fallos:")
            for f in failures[:5]:
                print(f"  {f['document_id'][:8]} ({f['status']}): {f.get('error', '')[:120]}")

        print(f"\n{'='*70}\n")


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 10
    asyncio.run(main(n))
