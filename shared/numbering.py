
from datetime import datetime, timezone
from typing import Tuple, Optional
import asyncpg
import uuid as _uuid_module
from database import get_conn
from shared.exceptions import ValidationError, StaleReservationError, SpecialLaneBusyError
from shared.logging import get_logger

logger = get_logger(__name__)


OFFICIAL_DOCUMENTS_LOCK_ID = 888888


async def _get_city_acronym(conn, schema_name: str) -> str:
    row = await conn.fetchrow(
        "SELECT acronym as city_acronym FROM public.municipalities WHERE schema_name = $1",
        schema_name
    )
    if not row:
        logger.error(
            "numbering.city_acronym_missing schema=%s — public.municipalities "
            "sin fila para el tenant; se aborta la numeración en vez de emitir 'UNK'",
            schema_name,
        )
        raise ValidationError(
            f"El tenant '{schema_name}' no tiene acrónimo de municipio configurado en "
            "public.municipalities. La numeración oficial no puede continuar."
        )
    return row['city_acronym']


async def _get_user_department(conn, user_id: str) -> Tuple[str, Optional[str]]:
    user_info = await conn.fetchrow(
        """
        SELECT
            d.acronym as dept_acronym,
            d.id as department_id
        FROM users u
        LEFT JOIN sectors s ON u.sector_id = s.id
        LEFT JOIN departments d ON s.department_id = d.id
        WHERE u.id = $1
        """,
        user_id
    )

    if not user_info:
        logger.error(f"Usuario {user_id[:8]}... no encontrado")
        raise ValidationError(f"Usuario {user_id} no encontrado en el sistema")

    if not user_info['dept_acronym']:
        logger.info("Usuario sin sector, usando departamento fallback")
        fallback = await conn.fetchrow(
            """
            SELECT d.acronym as dept_acronym, d.id as department_id
            FROM departments d
            WHERE d.is_active = true
            ORDER BY d.acronym, d.id
            LIMIT 1
            """
        )

        if not fallback or not fallback['dept_acronym']:
            logger.error(
                "numbering.user_department_fallback_missing user=%s — sin sector "
                "propio y sin departamento activo con acronym para usar como "
                "fallback; se aborta la numeración en vez de emitir 'UNK'",
                user_id[:8],
            )
            raise ValidationError(
                f"El usuario {user_id} no tiene sector asignado y no hay "
                "ningún departamento activo con acrónimo configurado para usar "
                "como fallback. La numeración oficial no puede continuar."
            )

        dept_acronym = fallback['dept_acronym']
        department_id = fallback['department_id']
    else:
        dept_acronym = user_info['dept_acronym']
        department_id = user_info['department_id']

    return dept_acronym, department_id


async def _get_tad_department(conn) -> Tuple[str, str]:
    row = await conn.fetchrow(
        "SELECT acronym as dept_acronym, id as department_id FROM departments "
        "WHERE is_system = true AND acronym = 'TAD' LIMIT 1"
    )
    if not row:
        raise ValidationError(
            "Departamento de sistema TAD no encontrado en este schema "
            "(migracion 087 de GDI-130 pendiente de aplicar)"
        )
    return row['dept_acronym'], str(row['department_id'])


# ----------------------------------------------------------------------
# PRECONDICIÓN DE AMBIENTE (GDI-130): `official_documents.numerator_citizen`
# (UUID NULL FK->citizens, con `numerator_id` nullable + CHECK num_nonnulls=1)
# la agrega la migración 087 en TODOS los schemas del tenant, y la plantilla
# de schemas nuevos (GDI-BD/sql/03-create-municipio.sql) ya la incluye.
# Verificado 19/08/2026 en DEV, HML y PRD.
#
# Este módulo ASUME la columna presente (ya no hay introspección defensiva
# por request). ANTES DE UN /pase a un ambiente nuevo o restaurado, verificar:
#
#   SELECT n.nspname AS schema_sin_columna
#   FROM pg_namespace n
#   JOIN pg_class c ON c.relnamespace = n.oid AND c.relname = 'official_documents'
#   WHERE NOT EXISTS (
#       SELECT 1 FROM information_schema.columns col
#       WHERE col.table_schema = n.nspname
#         AND col.table_name = 'official_documents'
#         AND col.column_name = 'numerator_citizen');
#
# (debe devolver 0 filas). Si falta en algún schema, numerar como ciudadano
# revienta con NotNullViolationError/ForeignKeyViolationError de asyncpg.
# ----------------------------------------------------------------------


async def _next_global_sequence(conn, year: int, *, caller: str) -> int:
    recycled = await conn.fetchrow(
        """
        SELECT id, global_sequence
        FROM official_documents
        WHERE year = $1
          AND reservation_status = 'CANCELLED'
          AND numbering_regime = 'GLOBAL'
          AND global_sequence IS NOT NULL
        ORDER BY reserved_at ASC
        LIMIT 1
        FOR UPDATE SKIP LOCKED
        """,
        year
    )

    if recycled:
        next_number = recycled['global_sequence']
        await conn.execute(
            "DELETE FROM document_chunks WHERE official_document_id = $1",
            recycled['id']
        )
        await conn.execute(
            "DELETE FROM official_documents WHERE id = $1 AND reservation_status = 'CANCELLED'",
            recycled['id']
        )
        logger.info(f"{caller}: reciclando global_sequence {next_number} (año {year})")
        return next_number

    result = await conn.fetchrow(
        """
        SELECT COALESCE(MAX(global_sequence), 0) + 1 as next_number
        FROM official_documents
        WHERE year = $1
          AND global_sequence IS NOT NULL
          AND reservation_status IN ('RESERVED','CONFIRMING','CONFIRMED')
        """,
        year
    )
    next_number = result['next_number']
    logger.info(f"{caller}: nuevo global_sequence {next_number} (año {year})")
    return next_number


async def reserve_citizen_number(
    document_type_acronym: str,
    citizen_id: str,
    year: int,
    *,
    schema_name: str,
    document_id: str,
    reference: str,
    document_type_id: str,
    content: dict,
    resume: str = None,
    signers: list = None,
    signer_sector_ids: list = None,
) -> Tuple[str, str, int, str]:
    logger.info(f"reserve_citizen_number: tipo={document_type_acronym} doc={document_id[:8]}... citizen={citizen_id[:8]}...")

    async with get_conn(schema_name=schema_name) as conn:
        city_acronym = await _get_city_acronym(conn, schema_name)
        dept_acronym, department_id = await _get_tad_department(conn)

        type_row = await conn.fetchrow(
            "SELECT id, special_numbering FROM document_types WHERE id = $1",
            document_type_id
        )
        if not type_row:
            raise ValidationError(f"Tipo de documento {document_type_id} no encontrado")

        if bool(type_row['special_numbering']):
            raise ValidationError(
                "Numeracion SPECIAL no soportada para documentos TAD en v1 "
                "(el tipo de documento no deberia tener special_numbering=true "
                "si es external_signable=true)"
            )

        logger.info(f"Adquiriendo advisory lock {OFFICIAL_DOCUMENTS_LOCK_ID} (municipio={schema_name})...")
        await conn.execute("SET LOCAL lock_timeout = '10s'")
        await conn.execute(
            f"SELECT pg_advisory_xact_lock({OFFICIAL_DOCUMENTS_LOCK_ID}, hashtext($1))",
            schema_name
        )

        existing_reserved = await conn.fetchrow(
            """
            SELECT id, official_number, department_id, global_sequence, reserved_at,
                   reservation_id
            FROM official_documents
            WHERE id = $1 AND reservation_status = 'RESERVED'
            """,
            document_id
        )
        if existing_reserved:
            now = datetime.now(timezone.utc)
            elapsed_minutes = (now - existing_reserved['reserved_at']).total_seconds() / 60
            if elapsed_minutes < GLOBAL_NUMBERING_TIMEOUT_MIN:
                logger.info(f"reserve_citizen_number: retry detectado, reutilizando {existing_reserved['official_number']}")
                return (
                    existing_reserved['official_number'],
                    str(existing_reserved['department_id']),
                    existing_reserved['global_sequence'],
                    str(existing_reserved['reservation_id']),
                )
            await conn.execute(
                "UPDATE official_documents SET reservation_status = 'CANCELLED' WHERE id = $1 AND reservation_status = 'RESERVED'",
                document_id
            )

        next_number = await _next_global_sequence(conn, year, caller="reserve_citizen_number")
        official_number = f"{document_type_acronym}-{year}-{next_number:08d}-{city_acronym}-{dept_acronym}"

        reservation_id_global = str(_uuid_module.uuid4())

        await conn.execute(
            """
            INSERT INTO official_documents (
                id, reference, content, official_number, year,
                department_id, numerator_citizen, signed_at,
                document_type_id, global_sequence,
                special_number, numbering_regime, reservation_status, reserved_at,
                signers, signer_sector_ids, resume, reservation_id
            ) VALUES (
                $1, $2, $3::jsonb, $4, $5,
                $6, $7, NULL,
                $8, $9,
                NULL, 'GLOBAL', 'RESERVED', NOW(),
                $10::jsonb, $11, $12, $13::uuid
            )
            """,
            document_id, reference, content, official_number, year,
            department_id, citizen_id,
            document_type_id, next_number,
            signers, signer_sector_ids, resume, reservation_id_global,
        )

        logger.info(f"reserve_citizen_number: reservado {official_number} (seq={next_number})")
        return official_number, department_id, next_number, reservation_id_global


async def _generate_confirmed_number(
    document_type_acronym: str,
    year: int,
    *,
    schema_name: str,
    document_id: str,
    reference: str,
    document_type_id: str,
    content: dict,
    resume: str = None,
    signers: list = None,
    signer_sector_ids: list = None,
    numerator_column: str,
    numerator_value: str,
    resolve_department,
    caller: str,
) -> Tuple[str, str, int]:
    if numerator_column not in ("numerator_id", "numerator_citizen"):
        raise ValueError(f"numerator_column inválida: {numerator_column}")

    logger.info(f"{caller}: generando número oficial para tipo: {document_type_acronym}")
    logger.info(f"Numerador ({numerator_column}): {numerator_value[:8]}..., Año: {year}")

    async with get_conn(schema_name=schema_name) as conn:
        city_acronym = await _get_city_acronym(conn, schema_name)
        dept_acronym, department_id = await resolve_department(conn)

        logger.info(f"Ciudad: {city_acronym}, Departamento: {dept_acronym}")

        logger.info(f"Adquiriendo advisory lock {OFFICIAL_DOCUMENTS_LOCK_ID} (municipio={schema_name})...")
        await conn.execute("SET LOCAL lock_timeout = '10s'")
        await conn.execute(
            f"SELECT pg_advisory_xact_lock({OFFICIAL_DOCUMENTS_LOCK_ID}, hashtext($1))",
            schema_name
        )

        next_number = await _next_global_sequence(conn, year, caller=caller)

        official_number = (
            f"{document_type_acronym}-{year}-{next_number:08d}"
            f"-{city_acronym}-{dept_acronym}"
        )

        logger.info(f"Número oficial generado: {official_number}")
        logger.info(f"Secuencia global: {next_number}")

        try:
            async with conn.transaction():
                await conn.execute(
                    f"""
                    INSERT INTO official_documents (
                        id,
                        reference,
                        content,
                        official_number,
                        year,
                        department_id,
                        {numerator_column},
                        signed_at,
                        document_type_id,
                        global_sequence,
                        reservation_status,
                        numbering_regime,
                        signers,
                        signer_sector_ids,
                        resume
                    ) VALUES (
                        $1, $2, $3::jsonb, $4, $5,
                        $6, $7, NULL,
                        $8, $9,
                        'CONFIRMED', 'GLOBAL',
                        $10::jsonb, $11, $12
                    )
                    """,
                    document_id,
                    reference,
                    content,
                    official_number,
                    year,
                    department_id,
                    numerator_value,
                    document_type_id,
                    next_number,
                    signers,
                    signer_sector_ids,
                    resume,
                )
        except asyncpg.UniqueViolationError as e:
            constraint_name = e.constraint_name or ''
            if constraint_name == 'official_documents_pkey':
                logger.info(f"Race detectado en INSERT (UniqueViolation PK), recuperando número del request paralelo")
                existing = await conn.fetchrow(
                    "SELECT official_number, global_sequence, department_id FROM official_documents WHERE id = $1",
                    document_id
                )
                if existing:
                    logger.info(f"Race detectado, reutilizando número del request paralelo: {existing['official_number']}")
                    return existing['official_number'], str(existing['department_id']), existing['global_sequence']
            logger.critical(
                f"numbering.duplicate_number_violation constraint={constraint_name} "
                f"schema={schema_name} doc={document_id}"
            )
            raise

        logger.info(f"Número reservado en BD: {official_number}")
        return official_number, str(department_id), next_number


async def generate_official_number(
    document_type_acronym: str,
    user_id: str,
    year: int,
    *,
    schema_name: str,
    document_id: str,
    reference: str,
    document_type_id: str,
    content: dict,
    resume: str = None,
    signers: list = None,
    signer_sector_ids: list = None,
) -> Tuple[str, str, int]:
    return await _generate_confirmed_number(
        document_type_acronym,
        year,
        schema_name=schema_name,
        document_id=document_id,
        reference=reference,
        document_type_id=document_type_id,
        content=content,
        resume=resume,
        signers=signers,
        signer_sector_ids=signer_sector_ids,
        numerator_column="numerator_id",
        numerator_value=user_id,
        resolve_department=lambda conn: _get_user_department(conn, user_id),
        caller="generate_official_number",
    )


async def generate_citizen_official_number(
    document_type_acronym: str,
    citizen_id: str,
    year: int,
    *,
    schema_name: str,
    document_id: str,
    reference: str,
    document_type_id: str,
    content: dict,
    resume: str = None,
    signers: list = None,
    signer_sector_ids: list = None,
) -> Tuple[str, str, int]:
    return await _generate_confirmed_number(
        document_type_acronym,
        year,
        schema_name=schema_name,
        document_id=document_id,
        reference=reference,
        document_type_id=document_type_id,
        content=content,
        resume=resume,
        signers=signers,
        signer_sector_ids=signer_sector_ids,
        numerator_column="numerator_citizen",
        numerator_value=citizen_id,
        resolve_department=_get_tad_department,
        caller="generate_citizen_official_number",
    )


SPECIAL_NUMBERING_TIMEOUT_MIN = 5
GLOBAL_NUMBERING_TIMEOUT_MIN = 4


def _columna_opcional(fila, nombre: str):
    try:
        return fila[nombre]
    except (KeyError, IndexError, TypeError):
        return None


async def _liberar_carril_special(conn, od, document_id: str) -> None:
    batch_id = _columna_opcional(od, 'batch_id')

    if batch_id:
        hermanos_vivos = await conn.fetchval(
            """
            SELECT COUNT(*)
            FROM official_documents
            WHERE batch_id = $1::uuid
              AND id <> $2::uuid
              AND reservation_status = 'RESERVED'
            """,
            str(batch_id), document_id,
        )
        if hermanos_vivos:
            logger.info(
                "numbering: la tanda %s todavía tiene %d reserva(s) viva(s) — "
                "el carril SPECIAL no se libera",
                str(batch_id)[:8], hermanos_vivos,
            )
            return

    await conn.execute(
        """
        UPDATE document_number_counters
        SET active_reservation_document_id = NULL,
            active_reservation_batch_id = NULL,
            updated_at = NOW()
        WHERE document_type_id = $1
          AND year = $2
          AND department_id = $3
          AND (active_reservation_document_id = $4
               OR ($5::uuid IS NOT NULL AND active_reservation_batch_id = $5::uuid))
        """,
        od['document_type_id'], od['year'], od['department_id'], document_id,
        str(batch_id) if batch_id else None,
    )


def _misma_tanda_y_mismo_usuario(
    *, counter_batch_id, batch_id: str, titular_user_id, user_id: str
) -> bool:
    if not counter_batch_id or not batch_id:
        return False
    if str(counter_batch_id) != str(batch_id):
        return False
    if not titular_user_id or not user_id:
        return False
    return str(titular_user_id) == str(user_id)


async def reserve_number(
    document_type_acronym: str,
    user_id: str,
    year: int,
    *,
    schema_name: str,
    document_id: str,
    reference: str,
    document_type_id: str,
    content: dict,
    resume: str = None,
    signers: list = None,
    signer_sector_ids: list = None,
    batch_id: str = None,
) -> Tuple[str, str, int, str]:
    logger.info(f"reserve_number: tipo={document_type_acronym} doc={document_id[:8]}...")

    async with get_conn(schema_name=schema_name) as conn:
        city_acronym = await _get_city_acronym(conn, schema_name)
        dept_acronym, department_id = await _get_user_department(conn, user_id)

        type_row = await conn.fetchrow(
            """
            SELECT id, special_numbering
            FROM document_types
            WHERE id = $1
            """,
            document_type_id
        )
        if not type_row:
            raise ValidationError(f"Tipo de documento {document_type_id} no encontrado")

        is_special = bool(type_row['special_numbering'])

        global_timeout_min = GLOBAL_NUMBERING_TIMEOUT_MIN

        if is_special:
            logger.info(f"reserve_number: regime=SPECIAL")

            counter_row = await conn.fetchrow(
                """
                INSERT INTO document_number_counters
                    (document_type_id, year, department_id, last_number, active_reservation_document_id)
                VALUES
                    ($1, $2, $3, 0, NULL)
                ON CONFLICT (document_type_id, year, department_id)
                DO UPDATE SET updated_at = NOW()
                RETURNING last_number, active_reservation_document_id,
                          active_reservation_batch_id
                """,
                document_type_id, year, department_id
            )
            counter_last_number = counter_row['last_number']
            active_doc_id = counter_row['active_reservation_document_id']

            if active_doc_id is not None:
                active_reservation = await conn.fetchrow(
                    """
                    SELECT id, reserved_at, special_number, numerator_id
                    FROM official_documents
                    WHERE id = $1 AND reservation_status = 'RESERVED'
                    """,
                    active_doc_id
                )
                try:
                    active_reservation_owner = (
                        active_reservation['numerator_id'] if active_reservation else None
                    )
                except (KeyError, IndexError):
                    active_reservation_owner = None

                if active_reservation:
                    if str(active_reservation['id']) == str(document_id):
                        existing = await conn.fetchrow(
                            """
                            SELECT official_number, department_id, special_number,
                                   reservation_id
                            FROM official_documents
                            WHERE id = $1
                            """,
                            document_id
                        )
                        logger.info(
                            f"reserve_number SPECIAL: retry detectado, "
                            f"reutilizando {existing['official_number']}"
                        )
                        return (
                            existing['official_number'],
                            str(existing['department_id']),
                            existing['special_number'],
                            str(existing['reservation_id']),
                        )
                    else:
                        if batch_id and _misma_tanda_y_mismo_usuario(
                            counter_batch_id=_columna_opcional(
                                counter_row, 'active_reservation_batch_id'
                            ),
                            batch_id=batch_id,
                            titular_user_id=active_reservation_owner,
                            user_id=user_id,
                        ):
                            logger.info(
                                "reserve_number SPECIAL: carril compartido con la "
                                "tanda %s (doc activo %s) — reserva hermana admitida",
                                str(batch_id)[:8], str(active_doc_id)[:8],
                            )
                        else:
                            logger.warning(
                                f"reserve_number SPECIAL: carril ocupado por "
                                f"{str(active_doc_id)[:8]}... "
                                f"terna tipo={document_type_id} depto={department_id} año={year}"
                            )
                            raise SpecialLaneBusyError(
                                document_type_id=str(document_type_id),
                                department_id=str(department_id),
                                year=year,
                            )

            recycled = await conn.fetchrow(
                """
                SELECT id, special_number
                FROM official_documents
                WHERE document_type_id = $1
                  AND department_id = $2
                  AND year = $3
                  AND reservation_status = 'CANCELLED'
                  AND numbering_regime = 'SPECIAL'
                ORDER BY reserved_at ASC
                LIMIT 1
                FOR UPDATE SKIP LOCKED
                """,
                document_type_id, department_id, year
            )

            if recycled:
                next_special_number = recycled['special_number']
                await conn.execute(
                    "DELETE FROM document_chunks WHERE official_document_id = $1",
                    recycled['id']
                )
                await conn.execute(
                    "DELETE FROM official_documents WHERE id = $1 AND reservation_status = 'CANCELLED'",
                    recycled['id']
                )
                logger.info(f"reserve_number SPECIAL: reciclando numero {next_special_number}")
            else:
                next_special_number = counter_last_number + 1
                logger.info(f"reserve_number SPECIAL: nuevo numero {next_special_number}")

            official_number = (
                f"{document_type_acronym}-{year}-{next_special_number:04d}"
                f"-{city_acronym}-{dept_acronym}"
            )

            await conn.execute(
                """
                DELETE FROM document_chunks
                WHERE official_document_id = $1::uuid
                  AND EXISTS (
                      SELECT 1 FROM official_documents
                      WHERE id = $1::uuid AND reservation_status = 'CANCELLED'
                  )
                """,
                document_id
            )
            await conn.execute(
                "DELETE FROM official_documents WHERE id = $1::uuid AND reservation_status = 'CANCELLED'",
                document_id
            )

            reservation_id_special = str(_uuid_module.uuid4())

            try:
                async with conn.transaction():
                    await conn.execute(
                        """
                        INSERT INTO official_documents (
                            id, reference, content, official_number, year,
                            department_id, numerator_id, signed_at,
                            document_type_id, global_sequence,
                            special_number, numbering_regime, reservation_status, reserved_at,
                            signers, signer_sector_ids, resume, reservation_id,
                            batch_id
                        ) VALUES (
                            $1, $2, $3::jsonb, $4, $5,
                            $6, $7, NULL,
                            $8, NULL,
                            $9, 'SPECIAL', 'RESERVED', NOW(),
                            $10::jsonb, $11, $12, $13::uuid,
                            $14::uuid
                        )
                        """,
                        document_id, reference, content, official_number, year,
                        department_id, user_id,
                        document_type_id,
                        next_special_number,
                        signers,
                        signer_sector_ids, resume, reservation_id_special,
                        batch_id,
                    )
            except asyncpg.UniqueViolationError:
                existing = await conn.fetchrow(
                    """SELECT official_number, department_id, special_number, reservation_id
                    FROM official_documents WHERE id = $1
                    AND reservation_status IN ('RESERVED', 'CONFIRMING', 'CONFIRMED')""",
                    document_id
                )
                if existing:
                    logger.info(
                        f"reserve_number SPECIAL: race PK detectado, "
                        f"reutilizando {existing['official_number']}"
                    )
                    return (
                        existing['official_number'],
                        str(existing['department_id']),
                        existing['special_number'],
                        str(existing['reservation_id']),
                    )
                raise

            await conn.execute(
                """
                UPDATE document_number_counters
                SET last_number = GREATEST(last_number, $1),
                    active_reservation_document_id = $2,
                    active_reservation_batch_id = $6::uuid,
                    updated_at = NOW()
                WHERE document_type_id = $3 AND year = $4 AND department_id = $5
                """,
                next_special_number, document_id, document_type_id, year, department_id,
                batch_id,
            )

            logger.info(f"reserve_number SPECIAL: reservado {official_number}")
            return official_number, str(department_id), next_special_number, reservation_id_special

        else:
            logger.info(f"reserve_number: regime=GLOBAL timeout={global_timeout_min}min")

            logger.info(f"Adquiriendo advisory lock {OFFICIAL_DOCUMENTS_LOCK_ID} (municipio={schema_name})...")
            await conn.execute("SET LOCAL lock_timeout = '10s'")
            await conn.execute(
                f"SELECT pg_advisory_xact_lock({OFFICIAL_DOCUMENTS_LOCK_ID}, hashtext($1))",
                schema_name
            )

            existing_reserved = await conn.fetchrow(
                """
                SELECT id, official_number, department_id, global_sequence, reserved_at,
                       reservation_id
                FROM official_documents
                WHERE id = $1 AND reservation_status = 'RESERVED'
                """,
                document_id
            )

            if existing_reserved:
                now = datetime.now(timezone.utc)
                reserved_at = existing_reserved['reserved_at']
                elapsed_minutes = (now - reserved_at).total_seconds() / 60

                if elapsed_minutes >= global_timeout_min:
                    logger.warning(
                        f"reserve_number GLOBAL: reserva {document_id[:8]}... expirada "
                        f"({elapsed_minutes:.1f}min > {global_timeout_min}min), "
                        f"marcando CANCELLED"
                    )
                    await conn.execute(
                        """
                        UPDATE official_documents
                        SET reservation_status = 'CANCELLED'
                        WHERE id = $1 AND reservation_status = 'RESERVED'
                        """,
                        document_id
                    )
                else:
                    logger.info(
                        f"reserve_number GLOBAL: retry detectado, "
                        f"reutilizando {existing_reserved['official_number']}"
                    )
                    return (
                        existing_reserved['official_number'],
                        str(existing_reserved['department_id']),
                        existing_reserved['global_sequence'],
                        str(existing_reserved['reservation_id']),
                    )

            next_number = await _next_global_sequence(conn, year, caller="reserve_number GLOBAL")

            official_number = (
                f"{document_type_acronym}-{year}-{next_number:08d}"
                f"-{city_acronym}-{dept_acronym}"
            )

            await conn.execute(
                """
                DELETE FROM document_chunks
                WHERE official_document_id = $1::uuid
                  AND EXISTS (
                      SELECT 1 FROM official_documents
                      WHERE id = $1::uuid AND reservation_status = 'CANCELLED'
                  )
                """,
                document_id
            )
            await conn.execute(
                "DELETE FROM official_documents WHERE id = $1::uuid AND reservation_status = 'CANCELLED'",
                document_id
            )

            reservation_id_global = str(_uuid_module.uuid4())

            try:
                async with conn.transaction():
                    await conn.execute(
                        """
                        INSERT INTO official_documents (
                            id, reference, content, official_number, year,
                            department_id, numerator_id, signed_at,
                            document_type_id, global_sequence,
                            special_number, numbering_regime, reservation_status, reserved_at,
                            signers, signer_sector_ids, resume, reservation_id
                        ) VALUES (
                            $1, $2, $3::jsonb, $4, $5,
                            $6, $7, NULL,
                            $8, $9,
                            NULL, 'GLOBAL', 'RESERVED', NOW(),
                            $10::jsonb, $11, $12, $13::uuid
                        )
                        """,
                        document_id, reference, content, official_number, year,
                        department_id, user_id,
                        document_type_id, next_number,
                        signers,
                        signer_sector_ids, resume, reservation_id_global,
                    )
            except asyncpg.UniqueViolationError as e:
                constraint_name = e.constraint_name or ''
                if constraint_name == 'official_documents_pkey':
                    existing = await conn.fetchrow(
                        """SELECT official_number, global_sequence, department_id, reservation_id
                        FROM official_documents WHERE id = $1
                        AND reservation_status IN ('RESERVED', 'CONFIRMING', 'CONFIRMED')""",
                        document_id
                    )
                    if existing:
                        logger.info(
                            f"reserve_number GLOBAL: race PK detectado, "
                            f"reutilizando {existing['official_number']}"
                        )
                        return (
                            existing['official_number'],
                            str(existing['department_id']),
                            existing['global_sequence'],
                            str(existing['reservation_id']),
                        )
                logger.critical(
                    f"numbering.duplicate_number_violation constraint={constraint_name} "
                    f"schema={schema_name} doc={document_id}"
                )
                raise

            logger.info(f"reserve_number GLOBAL: reservado {official_number} (seq={next_number})")
            return official_number, str(department_id), next_number, reservation_id_global


async def confirm_number(
    document_id: str,
    reservation_id: str,
    *,
    schema_name: str,
) -> None:
    logger.info(
        f"confirm_number: CAS RESERVED→CONFIRMING "
        f"doc={document_id[:8]}... ticket={reservation_id[:8]}..."
    )

    async with get_conn(schema_name=schema_name) as conn:
        od = await conn.fetchrow(
            """
            SELECT numbering_regime, document_type_id, department_id, year,
                   batch_id
            FROM official_documents
            WHERE id = $1 AND reservation_id = $2::uuid
            """,
            document_id, reservation_id
        )

        result = await conn.execute(
            """
            UPDATE official_documents
            SET reservation_status = 'CONFIRMING'
            WHERE id = $1 AND reservation_id = $2::uuid AND reservation_status = 'RESERVED'
            """,
            document_id, reservation_id
        )

        rows_updated = int(result.split()[-1]) if result else 0
        if rows_updated == 0:
            raise StaleReservationError(document_id, reservation_id)

        if od and od['numbering_regime'] == 'SPECIAL':
            await _liberar_carril_special(conn, od, document_id)

        logger.info(
            f"confirm_number: OK RESERVED→CONFIRMING "
            f"doc={document_id[:8]}... regime={od['numbering_regime'] if od else '?'}"
        )


async def finalize_number(
    document_id: str,
    reservation_id: str,
    *,
    schema_name: str,
) -> None:
    logger.info(
        f"finalize_number: CAS CONFIRMING→CONFIRMED "
        f"doc={document_id[:8]}... ticket={reservation_id[:8]}..."
    )

    async with get_conn(schema_name=schema_name) as conn:
        result = await conn.execute(
            """
            UPDATE official_documents
            SET reservation_status = 'CONFIRMED'
            WHERE id = $1 AND reservation_id = $2::uuid AND reservation_status = 'CONFIRMING'
            """,
            document_id, reservation_id
        )

        rows_updated = int(result.split()[-1]) if result else 0
        if rows_updated == 0:
            current = await conn.fetchrow(
                "SELECT reservation_status, reservation_id::text FROM official_documents WHERE id = $1::uuid",
                document_id
            )
            if current is None:
                logger.error(
                    f"finalize_number: doc={document_id[:8]}... NO EXISTE en official_documents "
                    f"ticket={reservation_id[:8]}... — corrupción o borrado inesperado (CRÍTICO)"
                )
            elif current['reservation_status'] == 'CONFIRMED':
                logger.warning(
                    f"finalize_number: doc={document_id[:8]}... ya está CONFIRMED "
                    f"(ticket actual={str(current['reservation_id'])[:8]}...) — soft-fail OK"
                )
            elif current['reservation_status'] == 'CANCELLED':
                logger.error(
                    f"finalize_number: doc={document_id[:8]}... está CANCELLED tras CONFIRMING "
                    f"— corrupción de estado (CRÍTICO). ticket={reservation_id[:8]}..."
                )
            else:
                logger.warning(
                    f"finalize_number: doc={document_id[:8]}... estado inesperado "
                    f"'{current['reservation_status']}' (ticket actual={str(current['reservation_id'])[:8]}..., "
                    f"ticket propio={reservation_id[:8]}...) — soft-fail"
                )
        else:
            logger.info(
                f"finalize_number: OK CONFIRMING→CONFIRMED doc={document_id[:8]}..."
            )


MOTIVOS_SIN_ALERTA = frozenset({
    "cancelled_by_user",
    "digital_session_expired",
    "la tanda venció sin completarse",
    "la tanda cayó y quedó sin limpiar",
})


def _es_final_normal(reason: str) -> bool:
    return (reason or "").strip() in MOTIVOS_SIN_ALERTA


async def cancel_number(
    document_id: str,
    *,
    schema_name: str,
    reason: str,
    reservation_id: str | None = None,
    alert: bool = True,
    from_states: tuple[str, ...] = ('RESERVED',),
) -> int:
    logger.info(f"cancel_number: doc={document_id[:8]}... reason={reason[:80]}")

    official_number = None
    regime = None
    rows_cancelled = 0

    async with get_conn(schema_name=schema_name) as conn:
        od = await conn.fetchrow(
            """
            SELECT id, numbering_regime, document_type_id, department_id, year,
                   official_number, batch_id
            FROM official_documents
            WHERE id = $1
            """,
            document_id
        )

        if not od:
            logger.warning(
                f"cancel_number: official_documents no encontrado para {document_id[:8]}..."
            )
        else:
            regime = od['numbering_regime']
            official_number = od['official_number']

            if reservation_id is not None:
                cancel_result = await conn.execute(
                    """
                    UPDATE official_documents
                    SET reservation_status = 'CANCELLED'
                    WHERE id = $1
                      AND reservation_status = ANY($3::text[])
                      AND reservation_id = $2::uuid
                    """,
                    document_id,
                    reservation_id,
                    list(from_states),
                )
            else:
                cancel_result = await conn.execute(
                    """
                    UPDATE official_documents
                    SET reservation_status = 'CANCELLED'
                    WHERE id = $1 AND reservation_status = ANY($2::text[])
                    """,
                    document_id,
                    list(from_states),
                )

            rows_cancelled = int(cancel_result.split()[-1]) if cancel_result else 0

            if regime == 'SPECIAL' and rows_cancelled > 0:
                await _liberar_carril_special(conn, od, document_id)

            if rows_cancelled > 0:
                logger.info(
                    f"cancel_number: OK doc={document_id[:8]}... "
                    f"numero={official_number} reason={reason[:200]}"
                )
            else:
                logger.info(
                    f"cancel_number: 0 filas afectadas (reserva fuera de "
                    f"{from_states} o ticket desactualizado) doc={document_id[:8]}... "
                    f"numero={official_number} reason={reason[:200]}"
                )

    if alert and not _es_final_normal(reason):
        try:
            from shared.alerts import send_alert_mail
            num_display = official_number or f"(sin registro — doc {document_id})"
            await send_alert_mail(
                subject=f"[GDI ALERTA] Firma fallida - {num_display}",
                body=(
                    f"Documento {document_id} falló al firmar (ambos intentos).\n"
                    f"Número cancelado: {num_display}\n"
                    f"Régimen: {regime or '(desconocido)'}\n"
                    f"Motivo: {reason}\n"
                    f"Schema: {schema_name}\n"
                    f"El número queda CANCELLED y puede ser reciclado en la próxima firma del mismo scope."
                ),
                schema_name=schema_name
            )
        except Exception as e:
            logger.error(f"cancel_number: error enviando alerta (soft-fail): {e}")

    return rows_cancelled
