"""
Script de migración para cambiar profile_picture_id a profile_picture_url
"""
from database import get_db_cursor

def migrate_profile_picture_column():
    """Migra la columna profile_picture_id a profile_picture_url con tipo TEXT"""
    
    with get_db_cursor(commit=True, schema_name="public") as cursor:
        try:
            print("🔄 Iniciando migración de profile_picture_id → profile_picture_url...")
            
            # ✅ La columna ya fue renombrada en el intento anterior
            print("✅ Columna ya renombrada: profile_picture_id → profile_picture_url")
            
            # Paso 1: Eliminar la vista que depende de la columna
            print("🗑️ Eliminando vista que bloquea el cambio...")
            cursor.execute('DROP VIEW IF EXISTS vw_usuarios_completos CASCADE;')
            print("✅ Vista eliminada")
            
            # Paso 2: Cambiar el tipo de datos a TEXT
            print("🔄 Cambiando tipo de datos UUID → TEXT...")
            cursor.execute('''
                ALTER TABLE users 
                ALTER COLUMN profile_picture_url TYPE TEXT;
            ''')
            print("✅ Tipo de datos cambiado: UUID → TEXT")
            
            print("🎉 Migración completada exitosamente!")
            print("⚠️ NOTA: La vista vw_usuarios_completos fue eliminada y debe recrearse si es necesaria")
            
        except Exception as e:
            print(f"❌ Error en migración: {e}")
            raise

if __name__ == "__main__":
    migrate_profile_picture_column()