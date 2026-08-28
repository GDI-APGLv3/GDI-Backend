
from pydantic import BaseModel, Field
from typing import Optional


class AutoLinkResult(BaseModel):

    case_id: str = Field(..., description="UUID del expediente propuesto")
    case_number: Optional[str] = Field(
        None,
        description="Número legible del expediente (ej. EE-2025-00001-MUN-SEC). "
                    "Disponible incluso cuando linked=False gracias al JOIN en la query.",
    )
    linked: bool = Field(..., description="True si el vínculo se concretó")
    reason: Optional[str] = Field(
        None,
        description=(
            "Código de fallo opaco (sin_permiso | sector_no_encontrado | "
            "duplicado | expediente_inactivo | error_interno). "
            "None cuando linked=True."
        ),
    )


class SuperSignRequest(BaseModel):

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

    official_number: Optional[str] = Field(
        None,
        description="Número oficial asignado al documento (solo presente si is_numerator=true)"
    )

    signed_pdf_url: Optional[str] = Field(
        None,
        description="URL del PDF firmado y numerado desde Legal Orchestrator (solo presente si is_numerator=true)"
    )

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

    auto_link_results: list[AutoLinkResult] = Field(
        default_factory=list,
        description=(
            "Resultado del auto-vínculo por expediente al numerar (vacío si no hubo "
            "propuestas auto_link o si el flujo es digital — en ese caso los resultados "
            "llegan por el endpoint /digital-signature/poll)."
        ),
    )
