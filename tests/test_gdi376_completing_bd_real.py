import uuid

import pytest
import pytest_asyncio

SCHEMA = "100_test"


@pytest_asyncio.fixture
async def db_ready():
    import database as db_module
    from database import init_pool, close_pool, test_connection

    if db_module.pool is not None:
        try:
            await close_pool()
        except Exception:
            pass
    try:
        await init_pool()
        ok = await test_connection()
    except Exception:
        ok = False
    if not ok:
        pytest.skip("Sin conexión a BD (DB_HOST/tunnel no disponible)")
    yield
    try:
        await close_pool()
    except Exception:
        pass


async def _terna_real():
    from database import fetch_one

    fila = await fetch_one(
        """
        SELECT document_type_id, department_id, year, numerator_id::text AS user_id
        FROM official_documents
        WHERE numbering_regime = 'GLOBAL' AND numerator_id IS NOT NULL
        ORDER BY created_at DESC
        LIMIT 1
        """,
        schema_name=SCHEMA,
    )
    if not fila:
        pytest.skip("100_test sin documentos oficiales de referencia")
    return dict(fila)


async def _armar_tanda(*, estado_sesion: str, estado_reserva: str):
    from database import execute

    t = await _terna_real()
    doc_id = str(uuid.uuid4())
    batch_id = str(uuid.uuid4())
    reservation_id = str(uuid.uuid4())
    session_id = "TEST" + uuid.uuid4().hex[:12].upper()
    numero = f"TEST-{t['year']}-{uuid.uuid4().hex[:8].upper()}"

    await execute(
        """
        INSERT INTO official_documents (
            id, reference, content, official_number, year, department_id,
            numerator_id, document_type_id, numbering_regime,
            reservation_status, reserved_at, reservation_id, batch_id
        ) VALUES (
            $1::uuid, 'test GDI-376 cierre en vuelo', '{}'::jsonb, $2, $3, $4::uuid,
            $5::uuid, $6, 'GLOBAL', $7, NOW(), $8::uuid, $9::uuid
        )
        """,
        doc_id, numero, t["year"], str(t["department_id"]), t["user_id"],
        t["document_type_id"], estado_reserva, reservation_id, batch_id,
        schema_name=SCHEMA,
    )
    await execute(
        """
        INSERT INTO public.digital_signature_sessions (
            session_id, file_id, schema_name, user_id, document_id,
            is_numerator, number, status, expires_at, reservation_id, batch_id
        ) VALUES (
            $1, $2, $3, $4::uuid, $5::uuid,
            true, $6, $7, NOW() + interval '10 minutes', $8::uuid, $9::uuid
        )
        """,
        session_id, "DATA" + session_id, SCHEMA, t["user_id"], doc_id,
        numero, estado_sesion, reservation_id, batch_id,
        schema_name="public",
    )
    return {
        "doc_id": doc_id, "batch_id": batch_id, "session_id": session_id,
        "numero": numero, "reservation_id": reservation_id,
    }


async def _limpiar(datos):
    from database import execute

    try:
        await execute(
            "DELETE FROM public.digital_signature_sessions WHERE batch_id = $1::uuid",
            datos["batch_id"], schema_name="public",
        )
        await execute(
            "DELETE FROM official_documents WHERE id = $1::uuid",
            datos["doc_id"], schema_name=SCHEMA,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"[limpieza] no se pudo borrar {datos['doc_id'][:8]}: {exc}")


async def _estado_reserva(doc_id):
    from database import fetch_one

    fila = await fetch_one(
        "SELECT reservation_status FROM official_documents WHERE id = $1::uuid",
        doc_id, schema_name=SCHEMA,
    )
    return fila["reservation_status"] if fila else None


async def _estado_sesion(session_id):
    from database import fetch_one

    fila = await fetch_one(
        """
        SELECT status, cancelled_at IS NOT NULL AS marcada
        FROM public.digital_signature_sessions WHERE session_id = $1
        """,
        session_id, schema_name="public",
    )
    return dict(fila) if fila else None


async def _cancelar_sin_tocar_r2(batch_id, motivo):
    from unittest.mock import AsyncMock, patch

    from services.documents.signing.batch_digital import cancelar_tanda

    with patch("services.documents.signing.digital_completion.borrar_pdf_firmado",
               AsyncMock()), \
         patch("services.documents.signing.r2_lock.release_signing_lock_R2_fail",
               AsyncMock()):
        return await cancelar_tanda(batch_id, schema_name=SCHEMA, motivo=motivo)


@pytest.mark.asyncio
async def test_a_un_cierre_en_vuelo_no_se_le_cancela_el_numero(db_ready):
    datos = await _armar_tanda(estado_sesion="completing", estado_reserva="CONFIRMING")
    try:
        resultado = await _cancelar_sin_tocar_r2(datos["batch_id"], "cancelled_by_user")

        assert await _estado_reserva(datos["doc_id"]) == "CONFIRMING", (
            "se le cancelo el numero a un cierre en vuelo: si ese PDF llega a "
            "oficial/ queda publicado con un numero que volvio al pozo, y de ese "
            "bucket no se borra nada"
        )
        assert resultado["numeros_liberados"] == 0

        sesion = await _estado_sesion(datos["session_id"])
        assert sesion["marcada"], (
            "la marca de la sesion es el cerrojo que frena al worker antes de "
            "subir: sin ella la guarda no sirve de nada"
        )
        assert sesion["status"] == "failed"
    finally:
        await _limpiar(datos)


@pytest.mark.asyncio
async def test_una_hermana_ya_firmada_si_libera_su_numero(db_ready):
    datos = await _armar_tanda(estado_sesion="waiting_batch", estado_reserva="CONFIRMING")
    try:
        resultado = await _cancelar_sin_tocar_r2(datos["batch_id"], "cancelled_by_user")

        assert await _estado_reserva(datos["doc_id"]) == "CANCELLED", (
            "el numero de una hermana ya firmada no volvio al pozo: es el bug "
            "que GDI-376 vino a cerrar"
        )
        assert resultado["numeros_liberados"] == 1
    finally:
        await _limpiar(datos)


@pytest.mark.asyncio
async def test_una_reserva_todavia_en_reserved_tambien_se_libera(db_ready):
    datos = await _armar_tanda(estado_sesion="pending", estado_reserva="RESERVED")
    try:
        resultado = await _cancelar_sin_tocar_r2(datos["batch_id"], "cancelled_by_user")

        assert await _estado_reserva(datos["doc_id"]) == "CANCELLED"
        assert resultado["numeros_liberados"] == 1
    finally:
        await _limpiar(datos)


@pytest.mark.asyncio
async def test_el_guard_del_ticket_sigue_vivo_contra_la_base(db_ready):
    from database import execute

    datos = await _armar_tanda(estado_sesion="waiting_batch", estado_reserva="CONFIRMING")
    try:
        await execute(
            "UPDATE official_documents SET reservation_id = $2::uuid WHERE id = $1::uuid",
            datos["doc_id"], str(uuid.uuid4()), schema_name=SCHEMA,
        )

        resultado = await _cancelar_sin_tocar_r2(datos["batch_id"], "cancelled_by_user")

        assert await _estado_reserva(datos["doc_id"]) == "CONFIRMING", (
            "se cancelo una reserva cuyo ticket ya no era el nuestro: eso es "
            "pisarle el numero a un reintento en curso (guard H6)"
        )
        assert resultado["numeros_liberados"] == 0, (
            "0 filas canceladas no puede contarse como numero liberado"
        )
    finally:
        await _limpiar(datos)
