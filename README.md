> 🇬🇧 **English summary** — GDI Backend is the core REST API of **GDI (Gestión Documental Inteligente)**, an open-source (AGPL-3.0) document-management platform for local governments in Latin America: official documents, case files (*expedientes*), digital signature workflows (Argentine Digital Signature Law 25.506), a native MCP server for AI assistants, and multi-tenant object storage. Built with **FastAPI + PostgreSQL**. Live product: [gdilatam.com](https://www.gdilatam.com).

# GDI Backend - Sistema de Gestión Documental Inteligente

API REST para la gestión integral de documentos y expedientes en gobiernos locales de América Latina. Núcleo del sistema **GDI (Gestión Documental Inteligente)**: documentos oficiales, expedientes, firma digital (Ley 25.506 Argentina) y comunicaciones internas, con arquitectura multi-tenant.

**Stack:** Python 3.12 · FastAPI · PostgreSQL 17 (asyncpg, SQL crudo — sin ORM) · Pydantic v2 · Auth0/JWT (RS256) · Cloudflare R2

Producto en vivo: [gdilatam.com](https://www.gdilatam.com) · Documentación: [docs.gdilatam.com](https://docs.gdilatam.com)

---

## Características Principales

- **Gestión completa de documentos** — crear, editar, firmar y publicar documentos oficiales con numeración automática
- **Sistema de expedientes** — organización por casos, movimientos, transferencias y asignaciones entre sectores
- **Firma digital y electrónica** — múltiples firmantes, numerador, firma con token físico via [FirmadorGDI](https://github.com/GDI-AGPLv3/FirmadorGDI) (protocolo @firma 1.9)
- **Multi-tenant por schema** — un schema PostgreSQL por municipio, aislamiento estricto (`schema_name` keyword-only en toda la capa de datos)
- **MCP Server integrado** — 42 tools (33 lectura + 9 escritura) para asistentes IA (Claude, ChatGPT, Gemini) con OAuth 2.0 nativo (RFC 9728)
- **REST API** — API privada por API Key y API pública de solo lectura para información oficial (búsquedas, registros, normativa)
- **Documentos automáticos** — carátulas (CAEX) al crear expedientes y pases (PV) en transferencias
- **Registro Legajo Multipropósito (RLM)** — registros configurables por familia con historial y vínculos a expedientes/documentos
- **Almacenamiento S3-compatible** — PDFs en Cloudflare R2 (o MinIO en despliegues autoalojados)
- **Búsqueda avanzada** — full-text con `unaccent` + `pg_trgm`, y búsqueda semántica con pgvector (RAG)

---

## Arquitectura

```
Cliente (Frontend / MCP / REST) → FastAPI → Services → [PDFComposer | Notary | R2] → PostgreSQL
```

- **GDI-PDFComposer** — microservicio de generación de PDFs (Jinja2 + WeasyPrint)
- **GDI-Notary** — microservicio de firma digital PAdES (pyHanko)
- El backend se comunica con ambos por HTTP privado; son stateless y escalables por separado.

> En el espejo open-source, ambos microservicios están incluidos en `microservices/` (`notary/` y `pdfcomposer/`).

### Estructura del repo

```
GDI-Backend/
├── endpoints/           # API REST (controladores thin)
│   ├── cases/          # Expedientes
│   ├── documents/      # Documentos
│   ├── users/          # Usuarios
│   ├── notes/          # Notas y memos
│   ├── rlm/            # Registros / Legajos
│   ├── dashboard/      # Stats y feed
│   ├── sectors/        # Sectores / Departamentos
│   ├── system/         # Health, config
│   └── auth/           # Onboarding
├── services/           # Lógica de negocio
│   ├── cases/          # cover_creator (CAEX), transfer_document_creator (PV)
│   ├── documents/      # core, catalog, lifecycle, signing, retrieval, preview, pdf
│   ├── notes/, rlm/, users/, sectors/
│   ├── shared/         # pdfcomposer_api, notary_api, settings_utils
│   └── storage/        # cloudflare.py (R2)
├── api_gateway/        # MCP Server + REST API + OAuth discovery
├── middleware/         # tenant_middleware, rate_limit
├── migrations/         # Scripts SQL
├── schemas/, models/   # Pydantic
├── shared/             # exceptions, numbering, validation
├── database.py         # Pool asyncpg (lifespan, codecs json/jsonb)
├── main.py             # Entry point FastAPI
└── auth.py             # Auth0 / JWT
```

**Reglas de arquitectura:**
- Endpoints *thin*: solo validación y delegación a services
- Services contienen toda la lógica de negocio
- SQL crudo via `database.py` — **no hay ORM**
- `schema_name` es keyword-only en todas las funciones de BD (previene tenant leakage)

---

## Flujo de Firma

```
draft → sent_to_sign → [firman todos] → official
                    ↓
                 rejected
```

1. **Inicio de firma** — se genera el PDF con PDFComposer y se sube a R2 (bucket de pendientes)
2. **Firmantes comunes** — firma electrónica o digital (según `signature_policy` del tenant); la firma digital usa token físico via FirmadorGDI
3. **Numerador** — última firma: asigna número oficial `{ACRONIMO}-{AÑO}-{NUMERO:07d}-{MUNI}` (advisory lock, sin race conditions) y publica el documento en el bucket oficial
4. Manejo automático de error `FULLPAGE`: si no hay lugar para el sello, se agrega página

---

## MCP Server

Servidor MCP con OAuth 2.0 automático (RFC 9728 + RFC 8414), compatible con Claude Code, ChatGPT y Gemini. Producción: `https://mcp.gdilatam.com/mcp`.

- **33 tools de lectura**: búsqueda de expedientes/documentos/registros, historial con resúmenes IA, firmas pendientes, etc.
- **9 tools de escritura**: crear/guardar/proponer documentos, iniciar firma, rechazar, asignar expedientes, gestionar responsables
- El `user_id` siempre sale del JWT (no falsificable por el cliente); toda tool de escritura valida los mismos permisos que el frontend
- No expuesto por diseño: firmar, vincular documento oficial y subsanar (solo Frontend/REST)

También expone **REST API** (`/api/v1/*` con `X-API-Key` + `X-User-ID`) y **REST API pública** de solo lectura (`/api/v1/public/{muni}/*` con API Key de municipio) para integraciones server-to-server.

---

## Desarrollo Local

```bash
# Requisitos: Python 3.12, PostgreSQL 17 (ver GDI-BD para schema e init)
pip install -r requirements.txt
cp .env.example .env   # completar variables

uvicorn main:app --reload --port 8000
curl http://localhost:8000/health
```

### Variables de entorno principales

| Variable | Descripción |
|----------|-------------|
| `DATABASE_URL` | Connection string PostgreSQL |
| `AUTH0_DOMAIN` / `AUTH0_AUDIENCE` | Configuración Auth0 |
| `PDFCOMPOSER_URL` / `PDFCOMPOSER_API_KEY` | Microservicio de PDFs |
| `NOTARY_URL` / `NOTARY_API_KEY` | Microservicio de firma |
| `R2_ACCOUNT_ID` / `R2_ACCESS_KEY_ID` / `R2_SECRET_ACCESS_KEY` | Cloudflare R2 |
| `FRONTEND_URL` | Origen permitido para CORS |

Ver `ENV.md` y `.env.example` para el inventario completo.

---

## Deploy

El sistema corre en producción sobre **Fly.io** (backend + gateway + microservicios con networking privado) y frontends en Vercel, pero cualquier plataforma que soporte contenedores sirve:

```bash
flyctl deploy   # usa Dockerfile / fly.toml como plantilla
```

Para un despliegue autoalojado completo (docker-compose, MinIO local, guía paso a paso) ver **[GDI-OnPremise](https://github.com/GDI-AGPLv3/GDI-OnPremise)**.

---

## Repos Relacionados

| Repo | Qué es |
|------|--------|
| [GDI-Frontend](https://github.com/GDI-AGPLv3/GDI-Frontend) | Interfaz web (Next.js) |
| [GDI-BD](https://github.com/GDI-AGPLv3/GDI-BD) | Schema PostgreSQL multi-tenant + herramientas |
| [FirmadorGDI](https://github.com/GDI-AGPLv3/FirmadorGDI) | Cliente de firma con token físico (Go) |
| [GDI-OnPremise](https://github.com/GDI-AGPLv3/GDI-OnPremise) | Despliegue autoalojado con docker-compose |
| [GDI-Docs](https://github.com/GDI-AGPLv3/GDI-Docs) | Documentación técnica (MkDocs) |

---

## Licencia

Este proyecto se distribuye bajo licencia **GNU Affero General Public License v3.0 (AGPL-3.0)**.
Ver el archivo [LICENSE](./LICENSE) para el texto completo.

> La AGPL-3.0 requiere que cualquier modificación desplegada como servicio de red sea publicada bajo la misma licencia. Licenciamiento comercial disponible — contacto: info@gdilatam.com
