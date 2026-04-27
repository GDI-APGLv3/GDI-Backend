# Integración Backend con Firma PAdES

## Resumen

GDI-Notary ahora soporta **firma digital PAdES** (PDF Advanced Electronic Signature) además de la firma visual. La firma PAdES es criptográficamente válida y verificable en Adobe Reader.

---

## Cambios Requeridos en Backend

### 1. Nuevo parámetro obligatorio: `tenant_id`

El Backend **DEBE** enviar `tenant_id` en cada llamada a `/sign-pdf` para usar firma PAdES.

```python
# Archivo: app/services/notary_api.py (o similar)

async def sign_document(
    pdf_content: bytes,
    signer: User,
    document_number: str = None,
    city: str = None,
) -> bytes:
    """Firma un documento PDF usando Notary."""

    form_data = {
        "name": signer.full_name,
        "seal": signer.seal,
        "department": signer.department.name,
        "entity": signer.department.entity.name,
        "tenant_id": signer.tenant_id,  # ← NUEVO: obligatorio para PAdES
    }

    if document_number:
        form_data["document_number"] = document_number
        form_data["city"] = city

    response = await httpx.post(
        f"{NOTARY_URL}/sign-pdf",
        headers={"X-API-Key": NOTARY_API_KEY},
        files={"pdf_file": ("document.pdf", pdf_content, "application/pdf")},
        data=form_data,
    )

    return response.content
```

### 2. Obtener `tenant_id` del request

El `tenant_id` ya está disponible en el middleware de tenant:

```python
# En cualquier endpoint
tenant_id = request.state.tenant_id  # ej: "100_municipio"

# O desde el schema_name
tenant_id = request.state.schema_name
```

### 3. Header de respuesta: `X-Signature-Type`

Notary devuelve un header indicando el tipo de firma usada:

```python
response = await sign_document(...)

signature_type = response.headers.get("X-Signature-Type")
# "pades" = firma digital PAdES
# "visual" = firma visual (solo en TEST)
```

---

## Archivos a Modificar en Backend

| Archivo | Cambio |
|---------|--------|
| `app/services/notary_api.py` | Agregar `tenant_id` a llamadas |
| `app/services/signing.py` | Pasar `tenant_id` desde el signer |
| `app/services/numerator.py` | Pasar `tenant_id` en numeración |

---

## Comportamiento por Ambiente

| Ambiente | Sin `tenant_id` | Con `tenant_id` sin cert | Con `tenant_id` con cert |
|----------|-----------------|--------------------------|--------------------------|
| **TEST** | Firma visual | Firma visual (fallback) | Firma PAdES |
| **PRD** | Error 400 | Error 400 | Firma PAdES |

**En producción es OBLIGATORIO tener certificado configurado.**

---

## Ejemplo de Request

```bash
curl -X POST "https://notary.gdilatam.com/sign-pdf" \
  -H "X-API-Key: ${NOTARY_API_KEY}" \
  -F "pdf_file=@documento.pdf" \
  -F "name=Juan Perez" \
  -F "seal=Intendente" \
  -F "department=Intendencia" \
  -F "entity=Municipalidad del Futuro" \
  -F "tenant_id=100_municipio" \
  -o firmado.pdf
```

---

## Ejemplo de Response

**Headers:**
```
Content-Type: application/pdf
Content-Disposition: attachment; filename=documento.pdf
X-Signature-Type: pades
```

**Body:** PDF firmado digitalmente

---

## Diseño Visual de la Firma

La firma PAdES muestra:

```
JUAN PEREZ
Intendente
Intendencia
Municipalidad del Futuro
```

Al hacer **clic en la firma** en Adobe Reader, se muestra:
- Certificado: "GESTION DOCUMENTAL INTELIGENTE"
- Organización: "Municipalidad del Futuro"
- Fecha/hora de firma (timestamp TSA)
- Estado de validez

---

## Errores Posibles

| HTTP | Código | Causa | Solución |
|------|--------|-------|----------|
| 400 | CERTIFICATE_NOT_FOUND | No hay cert para tenant | Configurar certificado |
| 400 | CERTIFICATE_LOAD_ERROR | Cert corrupto o password mal | Regenerar certificado |
| 500 | PADES_ERROR | Error en firma | Ver logs de Notary |

---

## Verificación de Firma

Para verificar que la firma PAdES funciona:

1. Abrir PDF en Adobe Reader
2. Clic en la firma (área clickeable)
3. Ver panel de firma con:
   - "Firmado por: GESTION DOCUMENTAL INTELIGENTE"
   - "Fecha: 2026-01-31..."
   - "La firma es válida" (o advertencia de cert auto-firmado en test)

---

## Migración Gradual

### Paso 1: Agregar `tenant_id` (sin romper)
```python
# El parámetro es opcional en TEST
form_data["tenant_id"] = getattr(signer, 'tenant_id', None)
```

### Paso 2: Verificar en TEST
- Confirmar que firma PAdES funciona
- Verificar PDFs en Adobe Reader

### Paso 3: Hacer obligatorio en PRD
```python
if not signer.tenant_id:
    raise ValueError("tenant_id requerido para firma")
```

---

## Contacto

- **Notary**: GDI-Notary repo
- **Certificados**: Contactar DevOps para configuración de nuevos tenants
