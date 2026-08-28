
import asyncio
import uuid
from datetime import datetime, timezone

import pytest

SCHEMA = "100_test"
CONCURRENCIA = 8


@pytest.fixture(scope="function")
async def base():
    import database as db_module
    from database import init_pool, close_pool, fetch_one

    if db_module.pool is not None:
        try:
            await close_pool()
        except Exception:
            pass

    try:
        await init_pool()
        await fetch_one("SELECT 1 AS ok", schema_name="public")
    except Exception as e:
        pytest.skip(
            f"sin base de datos ({type(e).__name__}) — abrir el túnel: "
            "flyctl proxy 5433:5432 -a <your-postgres-app>"
        )

    yield

    try:
        await close_pool()
    except Exception:
        pass


async def _semilla(special: bool) -> dict:
    from database import fetch_one

    usuario = await fetch_one(
        """
        SELECT id::text
        FROM users
        WHERE estado = 1 AND sector_id IS NOT NULL
        ORDER BY created_at
        LIMIT 1
        """,
        schema_name=SCHEMA,
    )
    tipo = await fetch_one(
        """
        SELECT id, acronym
        FROM document_types
        WHERE COALESCE(special_numbering, false) = $1
        ORDER BY id
        LIMIT 1
        """,
        special,
        schema_name=SCHEMA,
    )
    if not usuario or not tipo:
        pytest.skip(
            f"el schema {SCHEMA} no tiene usuario activo y tipo "
            f"{'SPECIAL' if special else 'GLOBAL'} para la prueba"
        )
    return {"user_id": usuario["id"], "type_id": tipo["id"], "acronym": tipo["acronym"]}


async def _limpiar(doc_ids: list[str]) -> None:
    from database import execute

    if not doc_ids:
        return
    try:
        await execute(
            """
            UPDATE official_documents
            SET reservation_status = 'CANCELLED', updated_at = NOW()
            WHERE id = ANY($1::uuid[])
              AND reservation_status IN ('RESERVED', 'CONFIRMING')
            """,
            doc_ids,
            schema_name=SCHEMA,
        )
    except Exception:
        pass


async def _reservar(doc_id: str, semilla: dict) -> dict:
    from datetime import datetime, timezone
    from shared.numbering import reserve_number

    try:
        numero, dept_id, seq, reservation_id = await reserve_number(
            semilla["acronym"],
            semilla["user_id"],
            datetime.now(timezone.utc).year,
            schema_name=SCHEMA,
            document_id=doc_id,
            reference=f"GDI-176 prueba de concurrencia {doc_id[:8]}",
            document_type_id=semilla["type_id"],
            content={"test": "gdi176"},
        )
        return {"ok": True, "numero": numero, "seq": seq, "reservation_id": reservation_id}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


@pytest.mark.asyncio
@pytest.mark.usefixtures("base")
class TestMismoDocumentoEnParalelo:

    async def test_global_un_solo_numero(self):
        semilla = await _semilla(special=False)
        doc_id = str(uuid.uuid4())

        try:
            resultados = await asyncio.gather(
                *(_reservar(doc_id, semilla) for _ in range(CONCURRENCIA))
            )
            exitosos = [r for r in resultados if r["ok"]]

            assert exitosos, (
                "ningún intento reservó número. Errores: "
                + " | ".join(r["error"] for r in resultados if not r["ok"])
            )

            numeros = {r["numero"] for r in exitosos}
            assert len(numeros) == 1, (
                f"el mismo documento obtuvo {len(numeros)} números distintos: "
                f"{sorted(numeros)}. Eso es numeración duplicada."
            )

            from database import fetch_all
            filas = await fetch_all(
                "SELECT id::text FROM official_documents WHERE official_number = $1",
                numeros.pop(),
                schema_name=SCHEMA,
            )
            assert len(filas) == 1, f"{len(filas)} filas comparten el mismo número"
        finally:
            await _limpiar([doc_id])

    async def test_special_un_solo_numero(self):
        semilla = await _semilla(special=True)
        doc_id = str(uuid.uuid4())

        try:
            resultados = await asyncio.gather(
                *(_reservar(doc_id, semilla) for _ in range(CONCURRENCIA))
            )
            exitosos = [r for r in resultados if r["ok"]]
            assert exitosos, (
                "ningún intento reservó número en el carril SPECIAL. Errores: "
                + " | ".join(r["error"] for r in resultados if not r["ok"])
            )
            numeros = {r["numero"] for r in exitosos}
            assert len(numeros) == 1, (
                f"el mismo documento SPECIAL obtuvo {len(numeros)} números: {sorted(numeros)}"
            )
        finally:
            await _limpiar([doc_id])

    async def test_reintentos_comparten_la_misma_reserva(self):
        semilla = await _semilla(special=False)
        doc_id = str(uuid.uuid4())

        try:
            resultados = await asyncio.gather(
                *(_reservar(doc_id, semilla) for _ in range(CONCURRENCIA))
            )
            tickets = {r["reservation_id"] for r in resultados if r["ok"]}
            assert len(tickets) <= 1, f"se emitieron {len(tickets)} tickets: {tickets}"
        finally:
            await _limpiar([doc_id])


@pytest.mark.asyncio
@pytest.mark.usefixtures("base")
class TestDocumentosDistintosEnParalelo:

    async def test_global_no_repite_numeros(self):
        semilla = await _semilla(special=False)
        doc_ids = [str(uuid.uuid4()) for _ in range(CONCURRENCIA)]

        try:
            resultados = await asyncio.gather(
                *(_reservar(d, semilla) for d in doc_ids)
            )
            exitosos = [r for r in resultados if r["ok"]]
            fallidos = [r for r in resultados if not r["ok"]]

            assert len(exitosos) >= 2, (
                "se esperaba que al menos dos reservas concurrentes salieran bien. "
                "Errores: " + " | ".join(r["error"] for r in fallidos)
            )

            numeros = [r["numero"] for r in exitosos]
            assert len(numeros) == len(set(numeros)), (
                f"números repetidos entre documentos distintos: "
                f"{[n for n in numeros if numeros.count(n) > 1]}"
            )

        finally:
            await _limpiar(doc_ids)

    async def test_global_no_deja_numeros_calculados_sin_usar(self):
        from database import fetch_all

        semilla = await _semilla(special=False)
        doc_ids = [str(uuid.uuid4()) for _ in range(CONCURRENCIA)]

        try:
            resultados = await asyncio.gather(
                *(_reservar(d, semilla) for d in doc_ids)
            )
            mias = sorted(r["seq"] for r in resultados if r["ok"] and r["seq"])
            if len(mias) < 2:
                pytest.skip("no hubo suficientes reservas exitosas para evaluar huecos")

            assert len(mias) == len(set(mias)), f"secuencias repetidas: {mias}"

            filas = await fetch_all(
                """
                SELECT global_sequence AS seq
                FROM official_documents
                WHERE year = $1
                  AND global_sequence BETWEEN $2 AND $3
                """,
                datetime.now(timezone.utc).year, mias[0], mias[-1],
                schema_name=SCHEMA,
            )
            ocupadas = {f["seq"] for f in filas}
            huerfanos = [n for n in range(mias[0], mias[-1] + 1) if n not in ocupadas]

            assert not huerfanos, (
                f"números calculados que no le quedaron a ningún documento: "
                f"{huerfanos}. Mis reservas: {mias}"
            )
        finally:
            await _limpiar(doc_ids)


@pytest.mark.asyncio
@pytest.mark.usefixtures("base")
class TestUnSoloPdfPorNumero:

    async def test_no_hay_numeros_con_dos_documentos_oficiales(self):
        from database import fetch_all

        duplicados = await fetch_all(
            """
            SELECT official_number, COUNT(*) AS cuantos
            FROM official_documents
            WHERE official_number IS NOT NULL
              AND (signed_at IS NOT NULL OR reservation_status = 'CONFIRMED')
            GROUP BY official_number
            HAVING COUNT(*) > 1
            """,
            schema_name=SCHEMA,
        )
        assert not duplicados, (
            "hay números oficiales con más de un documento: "
            + ", ".join(f"{d['official_number']} (×{d['cuantos']})" for d in duplicados)
        )
