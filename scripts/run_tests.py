"""
Script para ejecutar todos los tests del sistema de expedientes
"""
import subprocess
import sys
import os

def install_dependencies():
    """Instala las dependencias de testing si no están instaladas"""
    print("📦 Instalando dependencias de testing...")
    try:
        subprocess.run([
            sys.executable, "-m", "pip", "install", 
            "pytest>=7.4.0", 
            "pytest-asyncio>=0.23.0", 
            "httpx>=0.25.0"
        ], check=True)
        print("✅ Dependencias instaladas correctamente")
    except subprocess.CalledProcessError as e:
        print(f"❌ Error instalando dependencias: {e}")
        return False
    return True

def run_tests():
    """Ejecuta todos los tests"""
    print("\n🧪 Ejecutando tests del sistema de expedientes...")
    
    # Cambiar al directorio del proyecto
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    
    try:
        # Ejecutar pytest con configuración verbose
        result = subprocess.run([
            sys.executable, "-m", "pytest", 
            "tests/",
            "-v",
            "--tb=short",
            "--asyncio-mode=auto"
        ], check=True)
        
        print("\n✅ Todos los tests ejecutados correctamente")
        return True
        
    except subprocess.CalledProcessError as e:
        print(f"\n❌ Algunos tests fallaron. Código de salida: {e.returncode}")
        return False

def run_specific_test_file(test_file):
    """Ejecuta un archivo específico de tests"""
    print(f"\n🧪 Ejecutando tests de {test_file}...")
    
    try:
        result = subprocess.run([
            sys.executable, "-m", "pytest", 
            f"tests/{test_file}",
            "-v",
            "--tb=short",
            "--asyncio-mode=auto"
        ], check=True)
        
        print(f"\n✅ Tests de {test_file} ejecutados correctamente")
        return True
        
    except subprocess.CalledProcessError as e:
        print(f"\n❌ Tests de {test_file} fallaron. Código de salida: {e.returncode}")
        return False

def main():
    """Función principal"""
    print("🚀 Sistema de Testing para Backend de Expedientes")
    print("=" * 50)
    
    # Verificar si estamos en el directorio correcto
    if not os.path.exists("tests"):
        print("❌ No se encontró el directorio 'tests'. Asegúrate de ejecutar desde el directorio del proyecto.")
        return
    
    # Instalar dependencias
    if not install_dependencies():
        return
    
    # Opciones de testing
    print("\nOpciones disponibles:")
    print("1. Ejecutar todos los tests")
    print("2. Ejecutar tests específicos")
    print("3. Salir")
    
    choice = input("\nSelecciona una opción (1-3): ").strip()
    
    if choice == "1":
        run_tests()
    elif choice == "2":
        print("\nArchivos de test disponibles:")
        test_files = [
            "test_basic.py - Tests básicos de configuración",
            "test_real_endpoints.py - Tests de endpoints principales reales", 
            "test_transfer_endpoints.py - Tests de transferencia y asignación",
            "test_system.py - Tests de sistema y salud reales",
            "test_cases_main.py - Tests originales principales",
            "test_cases_movements.py - Tests originales de movimientos",
            "test_cases_documents.py - Tests originales de documentos",
            "test_cases_permissions.py - Tests originales de permisos",
            "test_cases_audit.py - Tests originales de auditoría"
        ]
        
        for i, file_desc in enumerate(test_files, 1):
            print(f"{i}. {file_desc}")
        
        try:
            file_choice = int(input("\nSelecciona el archivo de test (1-9): ").strip())
            if 1 <= file_choice <= 9:
                test_file = test_files[file_choice - 1].split(" - ")[0]
                run_specific_test_file(test_file)
            else:
                print("❌ Opción inválida")
        except ValueError:
            print("❌ Por favor ingresa un número válido")
    elif choice == "3":
        print("👋 ¡Hasta luego!")
    else:
        print("❌ Opción inválida")

if __name__ == "__main__":
    main()