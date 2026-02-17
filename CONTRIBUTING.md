# Guia de Contribucion - GDI Backend

Gracias por tu interes en contribuir a GDI Backend. Este documento explica como participar en el desarrollo del proyecto.

## Configuracion del Entorno de Desarrollo

### Requisitos

- Python 3.12
- PostgreSQL 12+
- Git

### Instalacion

```bash
# 1. Hacer fork del repositorio en GitHub

# 2. Clonar tu fork
git clone https://github.com/<tu-usuario>/GDI-Backend.git
cd GDI-Backend

# 3. Crear entorno virtual
python3.12 -m venv .venv

# 4. Activar entorno virtual
source .venv/bin/activate      # Linux/Mac
.venv\Scripts\activate         # Windows

# 5. Instalar dependencias
pip install -r requirements.txt

# 6. Configurar variables de entorno
cp .env.example .env
# Editar .env con tus credenciales locales
```

### Ejecutar el Servidor

```bash
python main.py
```

El servidor estara disponible en `http://127.0.0.1:8000` con documentacion interactiva en `/docs`.

## Ejecutar Tests

```bash
# Todos los tests
python -m pytest

# Con reporte de cobertura
python -m pytest --cov=.

# Un archivo especifico
python -m pytest tests/test_archivo.py
```

Asegurate de que todos los tests pasen antes de enviar un PR.

## Como Enviar Cambios

1. **Fork** del repositorio en GitHub.
2. **Crear una rama** desde `main` con un nombre descriptivo:
   ```bash
   git checkout -b feat/mi-nueva-funcionalidad
   ```
3. **Implementar los cambios** siguiendo las guias de estilo descritas abajo.
4. **Ejecutar los tests** y verificar que pasen.
5. **Commit** con mensaje siguiendo el formato convencional (ver abajo).
6. **Push** a tu fork:
   ```bash
   git push origin feat/mi-nueva-funcionalidad
   ```
7. **Abrir un Pull Request** contra la rama `main` del repositorio principal.

## Guia de Estilo

### Estructura General

- **Endpoints** en `endpoints/` -- solo reciben requests y devuelven responses.
- **Logica de negocio** en `services/` -- toda la logica va aqui.
- **Modelos Pydantic** en `models/` o `schemas/` -- validacion de datos.
- **Modelos SQLAlchemy** en `models/` -- definicion de tablas.

### Convenciones de Codigo

| Elemento | Convencion | Ejemplo |
|----------|------------|---------|
| Endpoints | `kebab-case` plural | `/documents`, `/cases` |
| Modelos | `PascalCase` singular | `Document`, `Case` |
| Schemas | `{Model}{Action}` | `DocumentCreate`, `DocumentResponse` |
| Services | `{Model}Service` | `DocumentService` |

### Reglas Criticas

- **Type hints obligatorios** en todos los parametros y retornos de funciones.
- **`schema_name` siempre keyword-only** en funciones de base de datos:
  ```python
  # Correcto
  await get_documents(db, schema_name=schema_name)

  # Incorrecto -- nunca posicional
  await get_documents(db, schema_name)
  ```
- **Async everywhere** -- usar `async def` para endpoints y queries.
- **Pydantic para validacion** -- nunca confiar en inputs sin validar.

### Formato de Codigo

- Seguir los patrones existentes en el codebase.
- Usar f-strings para interpolacion de cadenas.
- Documentar funciones complejas con docstrings.

## Formato de Commits

Usamos [Conventional Commits](https://www.conventionalcommits.org/):

```
tipo(alcance): descripcion breve

[cuerpo opcional]
```

**Tipos permitidos:**

| Tipo | Uso |
|------|-----|
| `feat` | Nueva funcionalidad |
| `fix` | Correccion de bug |
| `docs` | Cambios en documentacion |
| `refactor` | Refactorizacion sin cambio de funcionalidad |
| `test` | Agregar o modificar tests |
| `chore` | Tareas de mantenimiento |

**Ejemplos:**

```
feat(back): agregar endpoint de exportacion de documentos
fix(back): corregir query de busqueda por sector
docs(back): actualizar documentacion de API
test(back): agregar tests para servicio de firma
```

## Reportar Issues

Si encuentras un bug o quieres proponer una mejora, abre un issue en:

https://github.com/GDI-APGLv3/GDI-Backend/issues

Incluye la mayor cantidad de contexto posible: pasos para reproducir, logs, version de Python, etc.

## Codigo de Conducta

Se espera que todas las interacciones sean respetuosas y constructivas. Contribuciones de cualquier nivel de experiencia son bienvenidas.

## Licencia

Al contribuir a este proyecto, aceptas que tus contribuciones se distribuiran bajo la licencia AGPL-v3 del proyecto.
