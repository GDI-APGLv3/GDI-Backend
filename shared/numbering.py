"""
Sistema centralizado de numeración de documentos oficiales.

Este módulo maneja la generación de números oficiales únicos para TODOS
los documentos del sistema (carátulas de expedientes, anexos, informes, etc.).

CARACTERÍSTICAS:
- Secuencia global compartida por todos los tipos de documentos
- Advisory lock para prevenir race conditions
- Lock ultra corto (10-20ms) para máximo paralelismo
- Reutiliza números en caso de rollback
- Thread-safe y libre de race conditions

ARQUITECTURA:
- Centralizado en shared/ para uso de todo el sistema
- Usado por: numerator.py, cover_creator.py, y cualquier otro servicio

EJEMPLO DE USO:
    >>> from shared.numbering import generate_official_number
    >>> number, dept_id, seq = await generate_official_number(
    ...     "CAEX", user_id, 2025, connection
    ... )
    >>> print(number)
    'CAEX-2025-00000001-SMG-ADGEN'
"""

from typing import Tuple, Optional
from database import get_db_connection
from shared.exceptions import ValidationError
from shared.logging import get_logger

logger = get_logger(__name__)


# Lock ID único para numeración de documentos oficiales
# Diferente del lock de casos (999999) para evitar conflictos
OFFICIAL_DOCUMENTS_LOCK_ID = 888888


async def generate_official_number(
    document_type_acronym: str,
    user_id: str,
    year: int,
    connection=None,
    *,
    schema_name: str
) -> Tuple[str, str, int]:
    """
    Genera un número oficial único para cualquier tipo de documento del sistema.

    Esta función es el ÚNICO punto centralizado para generar números oficiales.
    Todos los servicios (numerator, cover_creator, etc.) deben usar esta función.

    Formato del número:
        {TYPE}-{YEAR}-{SEQUENCE:08d}-{CITY}-{DEPT}

    Ejemplos:
        - Carátula: CAEX-2025-00000001-SMG-ADGEN
        - Anexo: ANEXO-2025-00000002-SMG-ADGEN
        - Informe: IF-2025-00000003-SMG-ADGEN

    IMPORTANTE - Secuencia Global:
        La secuencia (00000001, 00000002, etc.) es COMPARTIDA entre todos
        los tipos de documentos. Esto garantiza que no haya duplicados y
        que todos los documentos tengan un orden cronológico único.

    IMPORTANTE - Advisory Lock:
        Usa pg_advisory_lock para prevenir race conditions.
        El lock se mantiene SOLO durante SELECT MAX + INSERT (10-20ms).
        Luego se libera para permitir máximo paralelismo.

    IMPORTANTE - Reutilización de Números:
        Si una transacción hace rollback, el número insertado desaparece
        y queda disponible para la siguiente petición. Esto evita "huecos"
        permanentes en la numeración.

    Args:
        document_type_acronym: Acrónimo del tipo de documento
            - "CAEX" para carátulas de expedientes
            - "ANEXO" para anexos
            - "IF" para informes
            - etc.
        user_id: UUID del usuario numerador
        year: Año actual (típicamente datetime.now().year)
        connection: (Opcional) Conexión de BD existente.
            - Si se pasa, usa esa conexión (para transacciones atómicas)
            - Si es None, crea una nueva conexión

    Returns:
        Tupla con 3 elementos:
            - official_number (str): Número oficial completo
            - department_id (str): UUID del departamento
            - global_sequence (int): Secuencia numérica global

    Raises:
        ValidationError: Si el usuario no existe o faltan datos requeridos

    Flujo de ejecución:
        1. Obtener datos del usuario (ciudad, departamento)
        2. Adquirir advisory lock (BLOQUEA otras peticiones)
        3. Consultar MAX(global_sequence) de official_documents
        4. Calcular next_number = MAX + 1
        5. Generar número oficial formateado
        6. Liberar advisory lock (DESBLOQUEA para siguiente petición)
        7. Retornar datos para que el caller haga el INSERT

    Nota sobre el INSERT:
        Esta función NO hace el INSERT en official_documents.
        Solo genera el número. El caller es responsable de:
        - Hacer el INSERT con el número generado
        - Hacer commit o rollback según corresponda

    Examples:
        >>> # Uso desde numerator.py (con conexión propia)
        >>> conn = get_db_connection()
        >>> try:
        ...     number, dept, seq = await generate_official_number(
        ...         "ANEXO", user_id, 2025, conn
        ...     )
        ...     cursor.execute("INSERT INTO official_documents ...")
        ...     conn.commit()
        ... except:
        ...     conn.rollback()

        >>> # Uso desde cover_creator.py (con conexión externa)
        >>> async def create_cover(connection):
        ...     number, dept, seq = await generate_official_number(
        ...         "CAEX", user_id, 2025, connection
        ...     )
        ...     cursor.execute("INSERT INTO official_documents ...")
        ...     # NO commit aquí (lo hace el caller)
    """

    # Determinar si debemos crear nuestra propia conexión o usar la externa
    if connection is not None:
        # Usar conexión externa (el caller la maneja)
        cursor = connection.cursor()

        # ================================================================
        # PASO 1: OBTENER INFORMACIÓN DEL USUARIO
        # ================================================================
        logger.info(f"Generando número oficial para tipo: {document_type_acronym}")
        logger.info(f"Usuario: {user_id[:8]}..., Año: {year}")

        # Obtener acrónimo del municipio desde public.municipalities
        city_acronym_query = """
            SELECT acronym as city_acronym
            FROM public.municipalities
            WHERE schema_name = %s
        """
        cursor.execute(city_acronym_query, (schema_name,))
        city_result = cursor.fetchone()
        city_acronym = city_result['city_acronym'] if city_result else 'UNK'

        # Obtener información del departamento del usuario
        user_info_query = """
            SELECT
                d.acronym as dept_acronym,
                d.id as department_id
            FROM users u
            LEFT JOIN sectors s ON u.sector_id = s.id
            LEFT JOIN departments d ON s.department_id = d.id
            WHERE u.id = %s
        """
        cursor.execute(user_info_query, (user_id,))
        user_info = cursor.fetchone()

        if not user_info:
            logger.error(f"Usuario {user_id[:8]}... no encontrado")
            raise ValidationError(f"Usuario {user_id} no encontrado en el sistema")

        # Usar fallback si el usuario no tiene sector asignado
        if not user_info['dept_acronym']:
            logger.info(f"Usuario sin sector, usando departamento fallback")
            cursor.execute("""
                SELECT d.acronym as dept_acronym, d.id as department_id
                FROM departments d
                WHERE d.is_active = true
                LIMIT 1
            """)
            fallback = cursor.fetchone()

            if not fallback:
                raise ValidationError("No hay departamentos activos en el sistema")

            dept_acronym = user_info['dept_acronym'] or fallback['dept_acronym'] or 'UNK'
            department_id = user_info['department_id'] or fallback['department_id']
        else:
            dept_acronym = user_info['dept_acronym']
            department_id = user_info['department_id']

        logger.info(f"Ciudad: {city_acronym}, Departamento: {dept_acronym}")

        # ================================================================
        # PASO 2: GENERAR NÚMERO CON ADVISORY LOCK ULTRA CORTO
        # ================================================================

        # Adquirir lock exclusivo (solo 10-20ms)
        # Este lock previene race conditions en SELECT MAX + INSERT
        logger.info(f"Adquiriendo advisory lock {OFFICIAL_DOCUMENTS_LOCK_ID}...")
        cursor.execute(f"SELECT pg_advisory_xact_lock({OFFICIAL_DOCUMENTS_LOCK_ID})")

        try:
            # Obtener siguiente número de secuencia GLOBAL
            # Este MAX es seguro porque estamos bajo lock
            next_number_query = """
                SELECT COALESCE(MAX(global_sequence), 0) + 1 as next_number
                FROM official_documents
                WHERE year = %s
                AND global_sequence IS NOT NULL
            """
            cursor.execute(next_number_query, (year,))
            result = cursor.fetchone()
            next_number = result['next_number']

            # Formatear número oficial completo
            official_number = f"{document_type_acronym}-{year}-{next_number:08d}-{city_acronym}-{dept_acronym}"

            logger.info(f"Número oficial generado: {official_number}")
            logger.info(f"Secuencia global: {next_number}")

        finally:
            # Lock se libera automáticamente al commit/rollback de la transacción
            # (ya no necesitamos unlock manual con pg_advisory_xact_lock)
            logger.info(f"Lock se liberará automáticamente al commit/rollback")

        return official_number, department_id, next_number

    else:
        # Crear nuestra propia conexión con context manager
        # IMPORTANTE: Pasar schema_name para compatibilidad con PgBouncer transaction mode
        with get_db_connection(schema_name) as conn:
            cursor = conn.cursor()

            # ================================================================
            # PASO 1: OBTENER INFORMACIÓN DEL USUARIO
            # ================================================================
            logger.info(f"Generando número oficial para tipo: {document_type_acronym}")
            logger.info(f"Usuario: {user_id[:8]}..., Año: {year}")

            # Obtener acrónimo del municipio desde public.municipalities
            city_acronym_query = """
                SELECT acronym as city_acronym
                FROM public.municipalities
                WHERE schema_name = %s
            """
            cursor.execute(city_acronym_query, (schema_name,))
            city_result = cursor.fetchone()
            city_acronym = city_result['city_acronym'] if city_result else 'UNK'

            # Obtener información del departamento del usuario
            user_info_query = """
                SELECT
                    d.acronym as dept_acronym,
                    d.id as department_id
                FROM users u
                LEFT JOIN sectors s ON u.sector_id = s.id
                LEFT JOIN departments d ON s.department_id = d.id
                WHERE u.id = %s
            """
            cursor.execute(user_info_query, (user_id,))
            user_info = cursor.fetchone()

            if not user_info:
                logger.error(f"Usuario {user_id[:8]}... no encontrado")
                raise ValidationError(f"Usuario {user_id} no encontrado en el sistema")

            # Usar fallback si el usuario no tiene sector asignado
            if not user_info['dept_acronym']:
                logger.info(f"Usuario sin sector, usando departamento fallback")
                cursor.execute("""
                    SELECT d.acronym as dept_acronym, d.id as department_id
                    FROM departments d
                    WHERE d.is_active = true
                    LIMIT 1
                """)
                fallback = cursor.fetchone()

                if not fallback:
                    raise ValidationError("No hay departamentos activos en el sistema")

                dept_acronym = user_info['dept_acronym'] or fallback['dept_acronym'] or 'UNK'
                department_id = user_info['department_id'] or fallback['department_id']
            else:
                dept_acronym = user_info['dept_acronym']
                department_id = user_info['department_id']

            logger.info(f"Ciudad: {city_acronym}, Departamento: {dept_acronym}")

            # ================================================================
            # PASO 2: GENERAR NÚMERO CON ADVISORY LOCK ULTRA CORTO
            # ================================================================

            # Adquirir lock exclusivo (solo 10-20ms)
            # Este lock previene race conditions en SELECT MAX + INSERT
            logger.info(f"Adquiriendo advisory lock {OFFICIAL_DOCUMENTS_LOCK_ID}...")
            cursor.execute(f"SELECT pg_advisory_xact_lock({OFFICIAL_DOCUMENTS_LOCK_ID})")

            try:
                # Obtener siguiente número de secuencia GLOBAL
                # Este MAX es seguro porque estamos bajo lock
                next_number_query = """
                    SELECT COALESCE(MAX(global_sequence), 0) + 1 as next_number
                    FROM official_documents
                    WHERE year = %s
                    AND global_sequence IS NOT NULL
                """
                cursor.execute(next_number_query, (year,))
                result = cursor.fetchone()
                next_number = result['next_number']

                # Formatear número oficial completo
                official_number = f"{document_type_acronym}-{year}-{next_number:08d}-{city_acronym}-{dept_acronym}"

                logger.info(f"Número oficial generado: {official_number}")
                logger.info(f"Secuencia global: {next_number}")

            finally:
                # Lock se libera automáticamente al commit/rollback de la transacción
                # (ya no necesitamos unlock manual con pg_advisory_xact_lock)
                logger.info(f"Lock se liberará automáticamente al commit/rollback")

            return official_number, department_id, next_number
