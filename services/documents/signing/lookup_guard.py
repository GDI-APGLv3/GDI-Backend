from shared.logging import get_logger
from database import fetch_val, fetch_one
from shared.exceptions import TransientLookupError, ValidationError

logger = get_logger("lookup_guard")


async def confirm_document_missing(document_id: str, *, schema_name: str, context: str) -> None:
    try:
        exists = await fetch_val(
            "SELECT 1 FROM document_draft WHERE id = $1::uuid LIMIT 1",
            document_id,
            schema_name=schema_name,
        )
    except Exception as e:
        logger.warning(
            "gdi250.confirm_failed context=%s doc=%s — no se pudo verificar, se asume transitorio: %s",
            context, document_id[:8], e,
        )
        raise TransientLookupError(
            "No se pudo verificar el estado del documento en este momento. "
            "Reintentá en unos segundos."
        )

    if exists:
        logger.warning(
            "gdi250.phantom_not_found context=%s doc=%s — la lectura volvió vacía pero el "
            "documento existe: se responde 503 (transitorio), NO 404",
            context, document_id[:8],
        )
        raise TransientLookupError(
            "El documento no está disponible en este momento. Reintentá en unos segundos."
        )

    logger.info(
        "gdi250.confirmed_missing context=%s doc=%s — confirmado inexistente, 404 legítimo",
        context, document_id[:8],
    )


async def confirm_user_missing(user_id: str, *, schema_name: str, context: str) -> None:
    try:
        exists = await fetch_val(
            "SELECT 1 FROM users WHERE id = $1::uuid LIMIT 1",
            user_id,
            schema_name=schema_name,
        )
    except Exception as e:
        logger.warning(
            "gdi250.confirm_failed context=%s user=%s — no se pudo verificar, se asume transitorio: %s",
            context, user_id[:8], e,
        )
        raise TransientLookupError(
            "No se pudo verificar el usuario en este momento. Reintentá en unos segundos."
        )

    if exists:
        logger.warning(
            "gdi250.phantom_not_found context=%s user=%s — la lectura volvió vacía pero el "
            "usuario existe: se responde 503 (transitorio), NO 400",
            context, user_id[:8],
        )
        raise TransientLookupError(
            "No se pudo validar tu usuario en este momento. Reintentá en unos segundos."
        )

    logger.info(
        "gdi250.confirmed_missing context=%s user=%s — confirmado inexistente",
        context, user_id[:8],
    )


async def confirm_signature_policy_missing(
    document_id: str, user_id: str, *, schema_name: str, context: str,
) -> None:
    try:
        exists = await fetch_val(
            """
            SELECT 1
            FROM document_signers ds
            JOIN document_draft dd ON ds.document_id = dd.id
            JOIN document_types dt ON dd.document_type_id = dt.id
            WHERE dd.id = $1::uuid AND ds.user_id = $2::uuid
            LIMIT 1
            """,
            document_id, user_id,
            schema_name=schema_name,
        )
    except Exception as e:
        logger.warning(
            "gdi276.confirm_failed context=%s doc=%s user=%s — no se pudo verificar "
            "signature_policy, se asume transitorio: %s",
            context, document_id[:8], user_id[:8], e,
        )
        raise TransientLookupError(
            "No se pudo verificar la política de firma en este momento. "
            "Reintentá en unos segundos."
        )

    if exists:
        logger.warning(
            "gdi276.phantom_policy_missing context=%s doc=%s user=%s — la lectura "
            "volvió vacía pero la fila existe: se responde 503 (NUNCA degradar a "
            "electrónica por default, sería un fraude de firma silencioso)",
            context, document_id[:8], user_id[:8],
        )
        raise TransientLookupError(
            "No se pudo determinar la política de firma en este momento. "
            "Reintentá en unos segundos."
        )

    logger.info(
        "gdi276.confirmed_policy_missing context=%s doc=%s user=%s — "
        "confirmado inexistente, error legítimo",
        context, document_id[:8], user_id[:8],
    )


async def resolve_signature_policy(
    document_id: str, user_id: str, *, schema_name: str, context: str,
) -> tuple[str, bool]:
    row = await fetch_one(
        """
        SELECT dt.signature_policy, ds.is_numerator
        FROM document_signers ds
        JOIN document_draft dd ON ds.document_id = dd.id
        JOIN document_types dt ON dd.document_type_id = dt.id
        WHERE dd.id = $1::uuid AND ds.user_id = $2::uuid
        """,
        document_id, user_id,
        schema_name=schema_name,
    )

    if not row:
        logger.warning(
            "gdi276.policy_row_missing context=%s doc=%s user=%s — confirmando",
            context, document_id[:8], user_id[:8],
        )
        await confirm_signature_policy_missing(
            document_id, user_id,
            schema_name=schema_name, context=context,
        )
        raise ValidationError(
            "No se encontró la política de firma para este documento y "
            "firmante (o el firmante no está asociado al documento)"
        )

    policy = row["signature_policy"]
    is_numerator = bool(row["is_numerator"])

    if not policy:
        logger.error(
            "gdi276.policy_null context=%s doc=%s user=%s — signature_policy "
            "es NULL/vacía en el tipo de documento; NO se degrada a electrónica",
            context, document_id[:8], user_id[:8],
        )
        raise ValidationError(
            "El tipo de documento no tiene configurada una política de firma "
            "(signature_policy es NULL). Corregí la configuración del tipo "
            "antes de firmar."
        )

    return policy, is_numerator
