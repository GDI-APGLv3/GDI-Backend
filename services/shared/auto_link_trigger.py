
from shared.logging import get_logger
from database import fetch_one, fetch_all, execute

logger = get_logger(__name__)

_EXCEPTION_REASON_MAP: dict[str, str] = {
    "AuthorizationError": "sin_permiso",
    "NotFoundError": "sector_no_encontrado",
    "ValidationError": "duplicado",
    "BusinessLogicError": "expediente_inactivo",
    "DatabaseError": "error_interno",
}


def _map_exception_to_reason(exc: Exception) -> str:
    exc_type = type(exc).__name__
    return _EXCEPTION_REASON_MAP.get(exc_type, "error_interno")


async def _get_user_sector_id(user_id: str, *, schema_name: str) -> str | None:
    row = await fetch_one(
        "SELECT sector_id FROM users WHERE id = $1 AND estado = 1",
        user_id,
        schema_name=schema_name,
    )
    if not row:
        return None
    return str(row["sector_id"]) if row["sector_id"] else None


async def collect_auto_link_results(document_id: str, *, schema_name: str) -> list[dict]:
    from services.case_queries import get_auto_link_proposals_query
    from services.case_queries import deactivate_proposed_document_query
    from services.cases.documents import link_official_document

    results: list[dict] = []

    logger.info(
        f"auto_link_trigger: collect iniciado doc={document_id[:8]}... schema={schema_name}"
    )

    try:
        proposals = await fetch_all(
            get_auto_link_proposals_query(),
            document_id,
            schema_name=schema_name,
        )
    except Exception as e:
        logger.error(
            f"auto_link_trigger: error consultando propuestas doc={document_id[:8]}...: {e}"
        )
        return results

    if not proposals:
        logger.debug(
            f"auto_link_trigger: sin propuestas auto_link para doc={document_id[:8]}..."
        )
        return results

    logger.info(
        f"auto_link_trigger: {len(proposals)} propuesta(s) a procesar doc={document_id[:8]}..."
    )

    for proposal in proposals:
        proposal_id = str(proposal["id"])
        case_id = str(proposal["case_id"])
        proposing_user_id = str(proposal["proposing_user_id"])
        case_number: str | None = proposal.get("case_number")

        try:
            user_sector_id = await _get_user_sector_id(
                proposing_user_id,
                schema_name=schema_name,
            )
            if not user_sector_id:
                logger.warning(
                    f"auto_link_trigger: sector no encontrado para "
                    f"user={proposing_user_id[:8]}... "
                    f"(propuesta={proposal_id[:8]}... queda pendiente)"
                )
                results.append({
                    "case_id": case_id,
                    "case_number": case_number,
                    "linked": False,
                    "reason": "sector_no_encontrado",
                })
                continue

            link = await link_official_document(
                case_id=case_id,
                official_document_id=document_id,
                linking_user_id=proposing_user_id,
                user_sector_id=user_sector_id,
                schema_name=schema_name,
                reason_override="Vinculación automática al numerar",
                auth_source="auto_link",
            )
            case_number = link.get("case_number") or case_number

            await execute(
                deactivate_proposed_document_query(),
                proposal_id,
                schema_name=schema_name,
                user_id=proposing_user_id,
                auth_source="auto_link",
            )

            logger.info(
                f"auto_link_trigger: propuesta={proposal_id[:8]}... vinculada OK "
                f"caso={case_id[:8]}... doc={document_id[:8]}..."
            )
            results.append({
                "case_id": case_id,
                "case_number": case_number,
                "linked": True,
                "reason": None,
            })

        except Exception as e:
            reason_code = _map_exception_to_reason(e)
            logger.warning(
                f"auto_link_trigger: propuesta={proposal_id[:8]}... FALLIDA "
                f"(queda pendiente) caso={case_id[:8]}... error={e}"
            )
            results.append({
                "case_id": case_id,
                "case_number": case_number,
                "linked": False,
                "reason": reason_code,
            })

    return results
