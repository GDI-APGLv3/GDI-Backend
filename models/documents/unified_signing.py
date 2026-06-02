"""
Modelos Pydantic para firma unificada de documentos.
Define el esquema de response para el endpoint super-sign que unifica
la firma de firmantes comunes y numeradores.
"""

from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime


class SuperSignRequest(BaseModel):
    """
    Body opcional para el endpoint POST /documents/{document_id}/super-sign.

    Permite al frontend indicar explicitamente el proveedor de firma,
    sobreescribiendo la politica del tipo de documento (signature_policy).

    Si no se envia body (o se envia `{}`), el backend usa la logica
    automatica basada en `signature_policy` de la BD.
    """

    provider_name: Optional[str] = Field(
        None,
        description=(
            "Proveedor de firma elegido por el usuario: "
            "'autofirma' (firma digital) | 'electronic' (firma electronica) | "
            "None (usar politica del tipo de documento)"
        ),
    )

    note: Optional[str] = Field(
        None,
        description="Nota opcional para adjuntar al proceso de firma",
    )


class SuperSignResponse(BaseModel):
    """
    Respuesta unificada para firma de documentos.
    Sirve tanto para firmantes comunes como para numeradores.

    ## Campos Comunes (siempre presentes):
    - success: Indica si la operación fue exitosa
    - message: Mensaje descriptivo del resultado
    - document_id: UUID del documento firmado
    - signature_id: UUID de la firma/numerador
    - document_status: Estado del documento después de firmar
    - signed_at: Timestamp de la firma
    - is_numerator: Indica si quien firmó es numerador

    ## Campos Solo para Numerador (opcionales):
    - official_number: Número oficial asignado al documento
    - signed_pdf_url: URL del PDF firmado y numerado

    ## Ejemplos:

    ### Firmante Común:
    ```json
    {
        "success": true,
        "message": "Documento firmado exitosamente",
        "document_id": "uuid-del-documento",
        "signature_id": "uuid-de-la-firma",
        "document_status": "sent_to_sign",
        "signed_at": "2025-01-15T10:30:00",
        "is_numerator": false,
        "official_number": null,
        "signed_pdf_url": null
    }
    ```

    ### Numerador:
    ```json
    {
        "success": true,
        "message": "Documento firmado y numerado exitosamente por el numerador",
        "document_id": "uuid-del-documento",
        "signature_id": "uuid-del-numerador",
        "document_status": "signed",
        "signed_at": "2025-01-15T10:35:00",
        "is_numerator": true,
        "official_number": "ANEXO-2025-00000002-SMG-ADGEN",
        "signed_pdf_url": "https://cloudflare-r2.../ANEXO-2025-00000002-SMG-ADGEN.pdf"
    }
    ```
    """

    success: bool = Field(
        ...,
        description="Indica si la firma fue exitosa"
    )

    message: str = Field(
        ...,
        description="Mensaje descriptivo del resultado de la operación"
    )

    document_id: str = Field(
        ...,
        description="UUID del documento firmado"
    )

    signature_id: str = Field(
        ...,
        description="UUID de la firma registrada (o UUID del numerador si es numerador)"
    )

    document_status: str = Field(
        ...,
        description="Estado del documento después de firmar (sent_to_sign o signed)"
    )

    signed_at: Optional[str] = Field(
        None,
        description="Timestamp de la firma en formato ISO (None si el flujo es digital y la firma aun no ocurrio)"
    )

    is_numerator: bool = Field(
        ...,
        description="Indica si quien firmó es numerador (true) o firmante común (false)"
    )

    # Campos opcionales solo para numerador
    official_number: Optional[str] = Field(
        None,
        description="Número oficial asignado al documento (solo presente si is_numerator=true)"
    )

    signed_pdf_url: Optional[str] = Field(
        None,
        description="URL del PDF firmado y numerado desde Legal Orchestrator (solo presente si is_numerator=true)"
    )

    # Campos opcionales para flujo de firma digital (Fase 2)
    flow: str = Field(
        "electronic",
        description="Flujo de firma: 'electronic' (Fase 1) o 'digital' (Fase 2 AutoFirma)"
    )

    session_id: Optional[str] = Field(
        None,
        description="ID de sesión de firma digital (solo flow=digital)"
    )

    poll_url: Optional[str] = Field(
        None,
        description="URL para pollear el estado de la sesión de firma digital"
    )

    user_payload: Optional[str] = Field(
        None,
        description="URI afirma:// para abrir AutoFirma en el cliente"
    )

    expires_at: Optional[str] = Field(
        None,
        description="Timestamp ISO de expiración de la sesión de firma digital"
    )
