import sys
sys.path.insert(0, 'Backend')

from database import get_db_connection
from shared.logging import get_logger

logger = get_logger(__name__)

def run_migration():
    """Ejecuta la migración de secuencia per-department a GLOBAL"""

    logger.info("=" * 60)
    logger.info("MIGRACIÓN: PER-DEPARTMENT → GLOBAL SEQUENCE")
    logger.info("=" * 60)

    try:
        # "public" explícito porque migraciones DDL afectan a todos los schemas
        with get_db_connection("public") as connection:
            with connection.cursor() as cursor:
                # 1. Eliminar constraint actual
                logger.info("\n[1/3] Eliminando constraint per-department...")
                cursor.execute("""
                    ALTER TABLE official_number_sequences
                    DROP CONSTRAINT uq_official_sequence
                """)
                logger.info("Constraint eliminado")

                # 2. Crear nuevo constraint GLOBAL
                logger.info("\n[2/3] Creando constraint GLOBAL...")
                cursor.execute("""
                    ALTER TABLE official_number_sequences
                    ADD CONSTRAINT uq_official_sequence
                    UNIQUE (document_type_acronym, year)
                """)
                logger.info("Constraint GLOBAL creado")

                # 3. Verificar
                logger.info("\n[3/3] Verificando cambios...")
                cursor.execute("""
                    SELECT a.attname AS column_name
                    FROM pg_constraint c
                    JOIN pg_attribute a ON a.attnum = ANY(c.conkey) AND a.attrelid = c.conrelid
                    WHERE c.conname = 'uq_official_sequence'
                    ORDER BY array_position(c.conkey, a.attnum)
                """)

                columns = [row['column_name'] for row in cursor.fetchall()]
                logger.info(f"\nColumnas en uq_official_sequence: {columns}")

                if columns == ['document_type_acronym', 'year']:
                    logger.info("\nMIGRACIÓN EXITOSA - Secuencia ahora es GLOBAL")
                else:
                    logger.error(f"\nERROR - Columnas inesperadas: {columns}")
                    connection.rollback()
                    return False

            # Commit final
            connection.commit()
            logger.info("\n" + "=" * 60)
            logger.info("MIGRACIÓN COMPLETADA CON ÉXITO")
            logger.info("=" * 60)
            return True

    except Exception as e:
        logger.error(f"\nERROR durante migración: {str(e)}")
        return False

if __name__ == "__main__":
    success = run_migration()
    sys.exit(0 if success else 1)
