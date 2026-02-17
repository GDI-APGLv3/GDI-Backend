"""
Script para monitorear conexiones activas a PostgreSQL.
Ejecutar para verificar que el connection pool está funcionando correctamente.

Uso:
    python scripts/monitor_connections.py
"""

import sys
import os

# Agregar parent directory al path para imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import get_db_connection

def monitor_connections():
    """Monitorea conexiones activas a PostgreSQL usando connection pool"""
    try:
        # Usar connection pool en lugar de conexión directa
        # "public" explícito porque pg_stat_activity es vista de sistema
        with get_db_connection("public") as conn:
            with conn.cursor() as cursor:
                query = """
                    SELECT
                        COUNT(*) as total_connections,
                        COUNT(*) FILTER (WHERE state = 'active') as active,
                        COUNT(*) FILTER (WHERE state = 'idle') as idle,
                        COUNT(*) FILTER (WHERE state = 'idle in transaction') as idle_in_transaction
                    FROM pg_stat_activity
                    WHERE datname = current_database()
                """

                cursor.execute(query)
                result = cursor.fetchone()

                print("=" * 60)
                print("POSTGRESQL CONNECTION MONITORING")
                print("=" * 60)
                print(f"Total connections:         {result[0]}")
                print(f"Active:                    {result[1]}")
                print(f"Idle:                      {result[2]}")
                print(f"Idle in transaction:       {result[3]}")
                print("-" * 60)
                print(f"Pool configured:           min=5, max=50")
                print(f"Railway Pro limit:         100 connections")
                print(f"Status:                    {'OK' if result[0] < 50 else 'WARNING - High usage'}")
                print("=" * 60)

                return result[0]

    except Exception as e:
        print(f"ERROR monitoring connections: {e}")
        return None

if __name__ == "__main__":
    total = monitor_connections()
    if total and total > 50:
        sys.exit(1)  # Exit with error if too many connections
    sys.exit(0)
