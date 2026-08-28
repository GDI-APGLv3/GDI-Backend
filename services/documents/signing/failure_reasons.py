
NO_AVISAR_AL_USUARIO: frozenset[str] = frozenset({
    "superseded",
    "document_already_signing",
    "duplicate_sign_common_session_gdi215",
    "duplicate_sign_citizen_session_gdi205",
})

_MOTIVOS: dict[str, tuple[str, str]] = {
    "stale_reservation": (
        "Se venció la reserva del número mientras se firmaba tu documento.",
        "El documento volvió a quedar pendiente de firma: lo tenés en «Para firmar».",
    ),
    "cas_confirm_failure": (
        "No pudimos confirmar la firma de tu documento.",
        "El documento volvió a quedar pendiente de firma: lo tenés en «Para firmar».",
    ),
    "numerator_partial_failure": (
        "La numeración de tu documento quedó incompleta.",
        "El documento volvió a quedar pendiente de firma: lo tenés en «Para firmar». "
        "Si vuelve a pasar, avisale a la mesa de ayuda.",
    ),
    "notary_business_error": (
        "El servicio de firma rechazó tu documento.",
        "El documento volvió a quedar pendiente de firma. Antes de reintentar, revisá "
        "que el archivo no esté dañado; si sigue fallando, avisale a la mesa de ayuda.",
    ),
    "pdf_integrity_failed": (
        "El PDF de tu documento no pasó el control de integridad, así que no se "
        "firmó (preferimos no firmar antes que firmar algo dudoso).",
        "El documento volvió a quedar pendiente de firma. Volvé a generarlo y firmalo "
        "de nuevo; si se repite, avisale a la mesa de ayuda.",
    ),
    "r2_object_locked": (
        "El documento ya tenía una versión definitiva guardada, así que la firma "
        "no se aplicó.",
        "Revisalo antes de reintentar: puede que ya esté firmado. Si sigue pendiente, "
        "lo tenés en «Para firmar».",
    ),
    "document_no_longer_signable": (
        "Tu documento cambió de estado mientras esperaba la firma y ya no se "
        "puede firmar.",
        "Abrilo en tus Documentos para ver cómo quedó.",
    ),
    "pending_expired_worker_offline": (
        "Tu documento no se llegó a firmar: el sistema estuvo demorado y la "
        "solicitud venció antes de que le llegara el turno.",
        "El documento volvió a quedar pendiente de firma: lo tenés en «Para firmar». "
        "Si el problema persiste, avisale a la mesa de ayuda.",
    ),
    "confirmed_and_rejected_conflict": (
        "Tu documento quedó con estados en conflicto (confirmado y rechazado a "
        "la vez), así que la firma se detuvo.",
        "Avisale a la mesa de ayuda con el número de documento: hace falta "
        "revisarlo a mano.",
    ),
}

_GENERICO = (
    "Tu documento no se pudo firmar.",
    "Volvió a quedar pendiente de firma: lo tenés en «Para firmar». Si vuelve a "
    "fallar, avisale a la mesa de ayuda.",
)


def motivo_humano(reason: str | None) -> tuple[str, str]:
    if not reason:
        return _GENERICO
    return _MOTIVOS.get(reason, _GENERICO)
