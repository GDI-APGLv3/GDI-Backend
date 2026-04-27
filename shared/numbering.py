"""
Sistema centralizado de numeración de documentos oficiales.

Este módulo maneja la generación de números oficiales únicos para TODOS
los documentos del sistema (carátulas de expedientes, anexos, informes, etc.).

CARACTERÍSTICAS:
- Secuencia global compartida por todos los tipos de documentos (GLOBAL)
- Secuencia especial por tipo+departamento+año (SPECIAL)
- Advisory lock para prevenir race conditions en GLOBAL
- Row-level lock en counter para SPECIAL
- Modelo RESERVED → CONFIRMED / CANCELLED para numerador.py
- generate_official_number() inserta directamente CONFIRMED (para CAEX/PV)

ARQUITECTURA:
- Centralizado en shared/ para uso de todo el sistema
- Usado por: numerator.py (via reserve/confirm/cancel), cover_creator.py (via generate)

EJEMPLO DE USO:
    >>> from shared.numbering import generate_official_number
    >>> number, dept_id, seq = await generate_official_number(
    ...     document_type_acronym="CAEX",
    ...     user_id=user_id,
    ...     year=2025,
    ...     schema_name=schema_name,
    ...     document_id=document_id,
    ...     reference=reference,
    ...     document_type_id=document_type_id,
    ...     content=cover_data,
    ...     signers=signers_data,
    ...     signer_sector_ids=sector_ids,
    ... )
    >>> print(number)
    'CAEX-2025-00000001-SMG-ADGEN'
"""

import json
from typing import Tuple, Optional, List
from database import get_db_connection
from shared.exceptions import ValidationError
from shared.logging import get_logger
from psycopg2.errors import UniqueViolation

logger = get_logger(__name__)


# Lock ID único para numeración de documentos oficiales
# Diferente del lock de casos (999999) para evitar conflictos
OFFICIAL_DOCUMENTS_LOCK_ID = 888888


def _get_city_acronym(cursor, schema_name: str) -> str:
    """
    Obtiene el acrónimo del municipio desde public.municipalities.

    Args:
        cursor: Cursor de psycopg2 activo.
        schema_name: Schema del tenant.

    Returns:
        Acrónimo del municipio, o 'UNK' si no se encuentra.
    """
    cursor.execute(
        "SELECT acronym as city_acronym FROM public.municipalities WHERE schema_name = %s",
        (schema_name,)
    )
    result = cursor.fetchone()
    return result['city_acronym'] if result else 'UNK'


def _get_user_department(cursor, user_id: str) -> Tuple[str, Optional[str]]:
    """
    Obtiene el acrónimo y UUID del departamento del usuario.

    Si el usuario no tiene sector asignado, usa el primer departamento
    activo como fallback (comportamiento original).

    Args:
        cursor: Cursor de psycopg2 activo.
        user_id: UUID del usuario numerador.

    Returns:
        Tupla (dept_acronym, department_id). Si no hay departamentos
        activos, retorna ('UNK', None).

    Raises:
        ValidationError: Si el usuario no existe en la BD.
    """
    cursor.execute(
        """
        SELECT
            d.acronym as dept_acronym,
            d.id as department_id
        FROM users u
        LEFT JOIN sectors s ON u.sector_id = s.id
        LEFT JOIN departments d ON s.department_id = d.id
        WHERE u.id = %s
        """,
        (user_id,)
    )
    user_info = cursor.fetchone()

    if not user_info:
        logger.error(f"Usuario {user_id[:8]}... no encontrado")
        raise ValidationError(f"Usuario {user_id} no encontrado en el sistema")

    if not user_info['dept_acronym']:
        logger.info("Usuario sin sector, usando departamento fallback")
        cursor.execute(
            """
            SELECT d.acronym as dept_acronym, d.id as department_id
            FROM departments d
            WHERE d.is_active = true
            LIMIT 1
            """
        )
        fallback = cursor.fetchone()

        if not fallback:
            raise ValidationError("No hay departamentos activos en el sistema")

        dept_acronym = user_info['dept_acronym'] or fallback['dept_acronym'] or 'UNK'
        department_id = user_info['department_id'] or fallback['department_id']
    else:
        dept_acronym = user_info['dept_acronym']
        department_id = user_info['department_id']

    return dept_acronym, department_id


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
    """
    Genera un número oficial único e inserta el registro en official_documents.

    Abre su PROPIA conexión corta. El advisory lock cubre solo:
        SELECT MAX(global_sequence) + INSERT + COMMIT (~5ms)

    El documento queda con signed_at=NULL hasta que la firma externa
    (Notary + R2) se complete. El caller debe hacer UPDATE posteriormente.

    Formato del número:
        {TYPE}-{YEAR}-{SEQUENCE:08d}-{CITY}-{DEPT}

    Ejemplos:
        - Carátula: CAEX-2025-00000001-SMG-ADGEN
        - Anexo:    ANEXO-2025-00000002-SMG-ADGEN
        - Informe:  IF-2025-00000003-SMG-ADGEN

    IMPORTANTE - Secuencia Global:
        La secuencia es COMPARTIDA entre todos los tipos de documentos.
        Garantiza orden cronológico único sin duplicados.

    IMPORTANTE - Huecos aceptables:
        Si la firma falla después del INSERT, el registro queda con
        signed_at=NULL. Eso es aceptable por diseño. Todas las queries
        de listado deben filtrar WHERE signed_at IS NOT NULL.

    Args:
        document_type_acronym: Acrónimo del tipo de documento (e.g. "CAEX", "IF").
        user_id: UUID del usuario numerador.
        year: Año de numeración (típicamente datetime.now().year).
        schema_name: Schema del tenant (keyword-only).
        document_id: UUID del documento a registrar.
        reference: Texto de referencia del documento.
        document_type_id: UUID del tipo de documento en document_types.
        content: Datos reales del documento (HTML, payload, etc.) para guardar
            como JSONB. Se guarda en MOMENTO 1 y NO se modifica en MOMENTO 2.
        resume: Resumen breve del documento (opcional). Solo numerator.py lo usa.
        signers: Lista de firmantes con status='pending' y signed_at=null.
            Si se pasa None, la columna queda en NULL.
        signer_sector_ids: Array de UUIDs de sectores de los firmantes.
            Si se pasa None, la columna queda en NULL.

    Returns:
        Tupla con 3 elementos:
            - official_number (str): Número oficial completo.
            - department_id (str): UUID del departamento del numerador.
            - global_sequence (int): Secuencia numérica global asignada.

    Raises:
        ValidationError: Si el usuario no existe, no hay departamentos activos,
            o el advisory lock no se puede adquirir dentro del timeout.
    """
    logger.info(f"Generando número oficial para tipo: {document_type_acronym}")
    logger.info(f"Usuario: {user_id[:8]}..., Año: {year}")

    with get_db_connection(schema_name) as conn:
        cursor = conn.cursor()
        try:
            # ================================================================
            # PASO 1: OBTENER DATOS DEL USUARIO Y MUNICIPIO
            # ================================================================
            city_acronym = _get_city_acronym(cursor, schema_name)
            dept_acronym, department_id = _get_user_department(cursor, user_id)

            logger.info(f"Ciudad: {city_acronym}, Departamento: {dept_acronym}")

            # ================================================================
            # PASO 2: ADVISORY LOCK + MAX+1 + INSERT + COMMIT (~5ms)
            # ================================================================
            logger.info(f"Adquiriendo advisory lock {OFFICIAL_DOCUMENTS_LOCK_ID}...")
            cursor.execute("SET LOCAL lock_timeout = '10s'")
            cursor.execute(f"SELECT pg_advisory_xact_lock({OFFICIAL_DOCUMENTS_LOCK_ID})")

            # Obtener siguiente número de secuencia GLOBAL (seguro bajo lock)
            cursor.execute(
                """
                SELECT COALESCE(MAX(global_sequence), 0) + 1 as next_number
                FROM official_documents
                WHERE year = %s
                AND global_sequence IS NOT NULL
                """,
                (year,)
            )
            result = cursor.fetchone()
            next_number = result['next_number']

            # Formatear número oficial completo
            official_number = (
                f"{document_type_acronym}-{year}-{next_number:08d}"
                f"-{city_acronym}-{dept_acronym}"
            )

            logger.info(f"Número oficial generado: {official_number}")
            logger.info(f"Secuencia global: {next_number}")

            # INSERT en official_documents con signed_at=NULL
            # El caller hará UPDATE cuando la firma externa se complete.
            # Para CAEX/PV (callers directos de generate_official_number), insertamos
            # directamente CONFIRMED sin pasar por RESERVED.
            try:
                cursor.execute(
                    """
                    INSERT INTO official_documents (
                        id,
                        reference,
                        content,
                        official_number,
                        year,
                        department_id,
                        numerator_id,
                        signed_at,
                        document_type_id,
                        global_sequence,
                        reservation_status,
                        numbering_regime,
                        signers,
                        signer_sector_ids,
                        resume
                    ) VALUES (
                        %s, %s, %s::jsonb, %s, %s,
                        %s, %s, NULL,
                        %s, %s,
                        'CONFIRMED', 'GLOBAL',
                        %s::jsonb, %s, %s
                    )
                    """,
                    (
                        document_id,
                        reference,
                        json.dumps(content),
                        official_number,
                        year,
                        department_id,
                        user_id,
                        document_type_id,
                        next_number,
                        json.dumps(signers) if signers is not None else None,
                        signer_sector_ids,
                        resume,
                    )
                )
            except UniqueViolation as e:
                # Race de doble-click: otro request ya insertó este document_id
                constraint_name = e.diag.constraint_name or ''
                if 'official_documents_pkey' in constraint_name or 'id' in constraint_name:
                    conn.rollback()
                    logger.info(f"Race detectado en INSERT (UniqueViolation PK), recuperando número del request paralelo")
                    cursor = conn.cursor()
                    cursor.execute(
                        "SELECT official_number, global_sequence, department_id FROM official_documents WHERE id = %s",
                        (document_id,)
                    )
                    existing = cursor.fetchone()
                    if existing:
                        logger.info(f"Race detectado, reusando número del request paralelo: {existing['official_number']}")
                        return existing['official_number'], existing['department_id'], existing['global_sequence']
                # Otra violación unique no esperada (bug): re-raise
                raise

            # COMMIT inmediato: libera el advisory_xact_lock (~5ms total)
            conn.commit()
            logger.info(f"Número reservado en BD: {official_number}")

            return official_number, department_id, next_number

        except UniqueViolation:
            # UniqueViolation ya manejada arriba (race PK) - si llega aquí es re-raise
            conn.rollback()
            raise
        except Exception:
            conn.rollback()
            raise


# ============================================================
# HELPERS PARA NUMERACION UNIFICADA (reserve/confirm/cancel)
# ============================================================

def _get_reservation_timeouts() -> tuple:
    """
    Timeouts de reserva de numeración.
    Retorna (special_minutes, global_minutes).
    Configurable via env vars, defaults: SPECIAL=5, GLOBAL=2.
    """
    import os
    special = int(os.getenv('SPECIAL_NUMBERING_RESERVATION_TIMEOUT_MINUTES', '5'))
    global_ = int(os.getenv('GLOBAL_NUMBERING_RESERVATION_TIMEOUT_MINUTES', '2'))
    return special, global_


# ============================================================
# RESERVE / CONFIRM / CANCEL
# ============================================================

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
) -> Tuple[str, str, int]:
    """
    Reserva un número oficial único. Branch interno según special_numbering del tipo.

    Para SPECIAL: usa counter por (document_type_id, year, department_id) con row-level lock.
    Para GLOBAL: usa advisory lock 888888 + MAX(global_sequence)+1.

    Detecta reintentos del mismo document_id y reutiliza la reserva existente.
    Recicla números de reservas CANCELLED (FIFO por reserved_at).

    Returns:
        (official_number, department_id, sequence)
        Para SPECIAL: sequence = special_number
        Para GLOBAL: sequence = global_sequence

    Raises:
        ValidationError: usuario no existe, conflicto de reserva activa no expirada,
                         lock timeout.
    """
    logger.info(f"reserve_number: tipo={document_type_acronym} doc={document_id[:8]}...")

    with get_db_connection(schema_name) as conn:
        cursor = conn.cursor()
        try:
            # PASO 1: Datos del usuario y municipio
            city_acronym = _get_city_acronym(cursor, schema_name)
            dept_acronym, department_id = _get_user_department(cursor, user_id)

            # PASO 2: Leer flag special_numbering de la tabla del TENANT (no global)
            cursor.execute(
                """
                SELECT id, special_numbering
                FROM document_types
                WHERE id = %s
                """,
                (document_type_id,)
            )
            type_row = cursor.fetchone()
            if not type_row:
                raise ValidationError(f"Tipo de documento {document_type_id} no encontrado")

            is_special = bool(type_row['special_numbering'])

            # PASO 3: Leer timeouts configurados
            special_timeout_min, global_timeout_min = _get_reservation_timeouts()

            if is_special:
                # ============================================================
                # BRANCH SPECIAL
                # ============================================================
                logger.info(f"reserve_number: regime=SPECIAL timeout={special_timeout_min}min")

                # 3a. INSERT ON CONFLICT DO UPDATE en counter para row-level lock
                cursor.execute(
                    """
                    INSERT INTO document_number_counters
                        (document_type_id, year, department_id, last_number, active_reservation_document_id)
                    VALUES
                        (%s, %s, %s, 0, NULL)
                    ON CONFLICT (document_type_id, year, department_id)
                    DO UPDATE SET updated_at = NOW()
                    RETURNING last_number, active_reservation_document_id
                    """,
                    (document_type_id, year, department_id)
                )
                counter_row = cursor.fetchone()
                counter_last_number = counter_row['last_number']
                active_doc_id = counter_row['active_reservation_document_id']

                # 3b. Chequear si hay reserva RESERVED activa para esta terna
                if active_doc_id is not None:
                    cursor.execute(
                        """
                        SELECT id, reserved_at, special_number
                        FROM official_documents
                        WHERE id = %s AND reservation_status = 'RESERVED'
                        """,
                        (active_doc_id,)
                    )
                    active_reservation = cursor.fetchone()

                    if active_reservation:
                        if str(active_reservation['id']) == str(document_id):
                            # Mismo document_id: retry del mismo usuario → reutilizar
                            cursor.execute(
                                """
                                SELECT official_number, department_id, special_number
                                FROM official_documents
                                WHERE id = %s
                                """,
                                (document_id,)
                            )
                            existing = cursor.fetchone()
                            conn.commit()
                            logger.info(
                                f"reserve_number SPECIAL: retry detectado, "
                                f"reutilizando {existing['official_number']}"
                            )
                            return (
                                existing['official_number'],
                                existing['department_id'],
                                existing['special_number'],
                            )
                        else:
                            # Otro document_id: verificar timeout
                            from datetime import datetime, timezone
                            now = datetime.now(timezone.utc)
                            reserved_at = active_reservation['reserved_at']
                            elapsed_minutes = (now - reserved_at).total_seconds() / 60

                            if elapsed_minutes < special_timeout_min:
                                conn.rollback()
                                raise ValidationError(
                                    f"Hay una firma en curso para este tipo de documento en su departamento. "
                                    f"Intente nuevamente en "
                                    f"{int(special_timeout_min - elapsed_minutes + 1)} minuto(s)."
                                )
                            else:
                                logger.warning(
                                    f"reserve_number SPECIAL: reserva {active_doc_id[:8]}... expirada "
                                    f"({elapsed_minutes:.1f}min > {special_timeout_min}min), "
                                    f"marcando CANCELLED"
                                )
                                cursor.execute(
                                    """
                                    UPDATE official_documents
                                    SET reservation_status = 'CANCELLED'
                                    WHERE id = %s AND reservation_status = 'RESERVED'
                                    """,
                                    (active_doc_id,)
                                )
                                cursor.execute(
                                    """
                                    UPDATE document_number_counters
                                    SET active_reservation_document_id = NULL
                                    WHERE document_type_id = %s AND year = %s AND department_id = %s
                                    """,
                                    (document_type_id, year, department_id)
                                )

                # 3c. Buscar CANCELLED de esta terna para reciclar (FIFO por reserved_at)
                cursor.execute(
                    """
                    SELECT id, special_number
                    FROM official_documents
                    WHERE document_type_id = %s
                      AND department_id = %s
                      AND year = %s
                      AND reservation_status = 'CANCELLED'
                      AND numbering_regime = 'SPECIAL'
                    ORDER BY reserved_at ASC
                    LIMIT 1
                    FOR UPDATE SKIP LOCKED
                    """,
                    (document_type_id, department_id, year)
                )
                recycled = cursor.fetchone()

                if recycled:
                    next_special_number = recycled['special_number']
                    cursor.execute(
                        "DELETE FROM official_documents WHERE id = %s AND reservation_status = 'CANCELLED'",
                        (recycled['id'],)
                    )
                    logger.info(f"reserve_number SPECIAL: reciclando numero {next_special_number}")
                else:
                    # 3d. Incrementar counter
                    next_special_number = counter_last_number + 1
                    logger.info(f"reserve_number SPECIAL: nuevo numero {next_special_number}")

                official_number = (
                    f"{document_type_acronym}-{year}-{next_special_number:04d}"
                    f"-{city_acronym}-{dept_acronym}"
                )

                # 3e. INSERT en official_documents
                try:
                    cursor.execute(
                        """
                        INSERT INTO official_documents (
                            id, reference, content, official_number, year,
                            department_id, numerator_id, signed_at,
                            document_type_id, global_sequence,
                            special_number, numbering_regime, reservation_status, reserved_at,
                            signers, signer_sector_ids, resume
                        ) VALUES (
                            %s, %s, %s::jsonb, %s, %s,
                            %s, %s, NULL,
                            %s, NULL,
                            %s, 'SPECIAL', 'RESERVED', NOW(),
                            %s::jsonb, %s, %s
                        )
                        """,
                        (
                            document_id, reference, json.dumps(content), official_number, year,
                            department_id, user_id,
                            document_type_id,
                            next_special_number,
                            json.dumps(signers) if signers is not None else None,
                            signer_sector_ids, resume,
                        )
                    )
                except UniqueViolation:
                    conn.rollback()
                    cursor = conn.cursor()
                    cursor.execute(
                        "SELECT official_number, department_id, special_number FROM official_documents WHERE id = %s",
                        (document_id,)
                    )
                    existing = cursor.fetchone()
                    if existing:
                        logger.info(
                            f"reserve_number SPECIAL: race PK detectado, "
                            f"reutilizando {existing['official_number']}"
                        )
                        return (
                            existing['official_number'],
                            existing['department_id'],
                            existing['special_number'],
                        )
                    raise

                # 3f. UPDATE counter: last_number y active_reservation_document_id
                cursor.execute(
                    """
                    UPDATE document_number_counters
                    SET last_number = GREATEST(last_number, %s),
                        active_reservation_document_id = %s,
                        updated_at = NOW()
                    WHERE document_type_id = %s AND year = %s AND department_id = %s
                    """,
                    (next_special_number, document_id, document_type_id, year, department_id)
                )

                conn.commit()
                logger.info(f"reserve_number SPECIAL: reservado {official_number}")
                return official_number, department_id, next_special_number

            else:
                # ============================================================
                # BRANCH GLOBAL
                # ============================================================
                logger.info(f"reserve_number: regime=GLOBAL timeout={global_timeout_min}min")

                # 4a. Advisory lock 888888
                logger.info(f"Adquiriendo advisory lock {OFFICIAL_DOCUMENTS_LOCK_ID}...")
                cursor.execute("SET LOCAL lock_timeout = '10s'")
                cursor.execute(f"SELECT pg_advisory_xact_lock({OFFICIAL_DOCUMENTS_LOCK_ID})")

                # 4b. Chequear si este document_id ya tiene reserva RESERVED (retry)
                cursor.execute(
                    """
                    SELECT id, official_number, department_id, global_sequence, reserved_at
                    FROM official_documents
                    WHERE id = %s AND reservation_status = 'RESERVED'
                    """,
                    (document_id,)
                )
                existing_reserved = cursor.fetchone()

                if existing_reserved:
                    from datetime import datetime, timezone
                    now = datetime.now(timezone.utc)
                    reserved_at = existing_reserved['reserved_at']
                    elapsed_minutes = (now - reserved_at).total_seconds() / 60

                    if elapsed_minutes >= global_timeout_min:
                        logger.warning(
                            f"reserve_number GLOBAL: reserva {document_id[:8]}... expirada "
                            f"({elapsed_minutes:.1f}min > {global_timeout_min}min), "
                            f"marcando CANCELLED"
                        )
                        cursor.execute(
                            """
                            UPDATE official_documents
                            SET reservation_status = 'CANCELLED'
                            WHERE id = %s AND reservation_status = 'RESERVED'
                            """,
                            (document_id,)
                        )
                        # Continuar: obtener número nuevo abajo
                    else:
                        # Retry válido: reutilizar número ya reservado
                        conn.commit()
                        logger.info(
                            f"reserve_number GLOBAL: retry detectado, "
                            f"reutilizando {existing_reserved['official_number']}"
                        )
                        return (
                            existing_reserved['official_number'],
                            existing_reserved['department_id'],
                            existing_reserved['global_sequence'],
                        )

                # 4c. Buscar CANCELLED de scope GLOBAL mismo año para reciclar (FIFO)
                cursor.execute(
                    """
                    SELECT id, global_sequence
                    FROM official_documents
                    WHERE year = %s
                      AND reservation_status = 'CANCELLED'
                      AND numbering_regime = 'GLOBAL'
                    ORDER BY reserved_at ASC
                    LIMIT 1
                    FOR UPDATE SKIP LOCKED
                    """,
                    (year,)
                )
                recycled_global = cursor.fetchone()

                if recycled_global:
                    next_number = recycled_global['global_sequence']
                    cursor.execute(
                        "DELETE FROM official_documents WHERE id = %s AND reservation_status = 'CANCELLED'",
                        (recycled_global['id'],)
                    )
                    logger.info(f"reserve_number GLOBAL: reciclando global_sequence {next_number}")
                else:
                    # 4d. MAX+1 seguro bajo advisory lock
                    cursor.execute(
                        """
                        SELECT COALESCE(MAX(global_sequence), 0) + 1 as next_number
                        FROM official_documents
                        WHERE year = %s
                        AND global_sequence IS NOT NULL
                        """,
                        (year,)
                    )
                    result = cursor.fetchone()
                    next_number = result['next_number']

                official_number = (
                    f"{document_type_acronym}-{year}-{next_number:08d}"
                    f"-{city_acronym}-{dept_acronym}"
                )

                # 4e. INSERT en official_documents (SIEMPRE: tanto reciclaje como nuevo)
                # Si fue reciclaje, el DELETE del CANCELLED ya se hizo en 4c.
                try:
                    cursor.execute(
                        """
                        INSERT INTO official_documents (
                            id, reference, content, official_number, year,
                            department_id, numerator_id, signed_at,
                            document_type_id, global_sequence,
                            special_number, numbering_regime, reservation_status, reserved_at,
                            signers, signer_sector_ids, resume
                        ) VALUES (
                            %s, %s, %s::jsonb, %s, %s,
                            %s, %s, NULL,
                            %s, %s,
                            NULL, 'GLOBAL', 'RESERVED', NOW(),
                            %s::jsonb, %s, %s
                        )
                        """,
                        (
                            document_id, reference, json.dumps(content), official_number, year,
                            department_id, user_id,
                            document_type_id, next_number,
                            json.dumps(signers) if signers is not None else None,
                            signer_sector_ids, resume,
                        )
                    )
                except UniqueViolation as e:
                    constraint_name = e.diag.constraint_name or ''
                    if 'official_documents_pkey' in constraint_name or 'id' in constraint_name:
                        conn.rollback()
                        cursor = conn.cursor()
                        cursor.execute(
                            "SELECT official_number, global_sequence, department_id FROM official_documents WHERE id = %s",
                            (document_id,)
                        )
                        existing = cursor.fetchone()
                        if existing:
                            logger.info(
                                f"reserve_number GLOBAL: race PK detectado, "
                                f"reutilizando {existing['official_number']}"
                            )
                            return (
                                existing['official_number'],
                                existing['department_id'],
                                existing['global_sequence'],
                            )
                    raise

                # 4f. COMMIT: libera advisory lock (~5ms total desde adquisición)
                conn.commit()
                logger.info(f"reserve_number GLOBAL: reservado {official_number} (seq={next_number})")
                return official_number, department_id, next_number

        except UniqueViolation:
            conn.rollback()
            raise
        except Exception:
            conn.rollback()
            raise


async def confirm_number(
    document_id: str,
    *,
    schema_name: str,
) -> None:
    """
    Confirma una reserva: cambia reservation_status de RESERVED a CONFIRMED.

    NO setea signed_at — eso lo hace el UPDATE existente en numerator.py.
    Si era SPECIAL, limpia active_reservation_document_id en el counter.
    Idempotente: si ya está CONFIRMED, no falla.
    """
    logger.info(f"confirm_number: doc={document_id[:8]}...")

    with get_db_connection(schema_name) as conn:
        cursor = conn.cursor()

        cursor.execute(
            """
            SELECT id, numbering_regime, document_type_id, department_id, year
            FROM official_documents
            WHERE id = %s
            """,
            (document_id,)
        )
        od = cursor.fetchone()

        if not od:
            logger.warning(
                f"confirm_number: official_documents no encontrado para {document_id[:8]}..."
            )
            return  # Soft-fail

        cursor.execute(
            """
            UPDATE official_documents
            SET reservation_status = 'CONFIRMED'
            WHERE id = %s AND reservation_status = 'RESERVED'
            """,
            (document_id,)
        )

        rows_updated = cursor.rowcount
        if rows_updated == 0:
            logger.warning(
                f"confirm_number: no se actualizaron filas para {document_id[:8]}... "
                f"(ya CONFIRMED o CANCELLED?)"
            )

        # Si era SPECIAL, limpiar el counter
        if od['numbering_regime'] == 'SPECIAL':
            cursor.execute(
                """
                UPDATE document_number_counters
                SET active_reservation_document_id = NULL,
                    updated_at = NOW()
                WHERE document_type_id = %s
                  AND year = %s
                  AND department_id = %s
                  AND active_reservation_document_id = %s
                """,
                (od['document_type_id'], od['year'], od['department_id'], document_id)
            )

        conn.commit()
        logger.info(
            f"confirm_number: OK doc={document_id[:8]}... regime={od['numbering_regime']}"
        )


async def cancel_number(
    document_id: str,
    *,
    schema_name: str,
    reason: str,
) -> None:
    """
    Cancela una reserva: cambia reservation_status de RESERVED a CANCELLED.

    El número queda disponible para reciclaje en la próxima llamada a reserve_number()
    del mismo scope. Si era SPECIAL, limpia active_reservation_document_id en el counter.
    Idempotente: si ya está CANCELLED, no falla.

    Envía mail de alerta (soft-fail) fuera de la transacción.
    """
    logger.info(f"cancel_number: doc={document_id[:8]}... reason={reason[:80]}")

    regime = None
    official_number = None

    with get_db_connection(schema_name) as conn:
        cursor = conn.cursor()

        cursor.execute(
            """
            SELECT id, numbering_regime, document_type_id, department_id, year, official_number
            FROM official_documents
            WHERE id = %s
            """,
            (document_id,)
        )
        od = cursor.fetchone()

        if not od:
            logger.warning(
                f"cancel_number: official_documents no encontrado para {document_id[:8]}..."
            )
            return

        regime = od['numbering_regime']
        official_number = od['official_number']

        cursor.execute(
            """
            UPDATE official_documents
            SET reservation_status = 'CANCELLED'
            WHERE id = %s AND reservation_status = 'RESERVED'
            """,
            (document_id,)
        )

        if regime == 'SPECIAL':
            cursor.execute(
                """
                UPDATE document_number_counters
                SET active_reservation_document_id = NULL,
                    updated_at = NOW()
                WHERE document_type_id = %s
                  AND year = %s
                  AND department_id = %s
                  AND active_reservation_document_id = %s
                """,
                (od['document_type_id'], od['year'], od['department_id'], document_id)
            )

        conn.commit()
        logger.info(
            f"cancel_number: OK doc={document_id[:8]}... "
            f"numero={official_number} reason={reason[:200]}"
        )

    # Fuera de transacción: mail de alerta (soft-fail)
    try:
        from shared.alerts import send_alert_mail
        await send_alert_mail(
            subject=f"[GDI ALERTA] Firma fallida - {official_number}",
            body=(
                f"Documento {document_id} falló al firmar (ambos intentos).\n"
                f"Número cancelado: {official_number}\n"
                f"Régimen: {regime}\n"
                f"Motivo: {reason}\n"
                f"Schema: {schema_name}\n"
                f"El número queda CANCELLED y puede ser reciclado en la próxima firma del mismo scope."
            ),
            schema_name=schema_name
        )
    except Exception as e:
        logger.error(f"cancel_number: error enviando alerta (soft-fail): {e}")
