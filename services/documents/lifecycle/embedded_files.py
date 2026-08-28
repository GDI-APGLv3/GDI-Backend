import uuid
from typing import Any, Dict, List, Optional, Tuple

from database import fetch_all, fetch_one, execute, transaction
from config.constants import (
    EDITABLE_DOCUMENT_STATES,
    MAX_EMBEDDED_FILE_SIZE,
    MAX_TOTAL_EMBEDDED_SIZE,
    MAX_EMBEDDED_FILES_PER_DOCUMENT,
    EMBEDDED_FILE_NOT_EDITABLE_ERROR,
    EMBEDDED_FILE_NOT_CREATOR_ERROR,
    EMBEDDED_FILE_TYPE_NOT_ALLOWED_ERROR,
    EMBEDDED_FILE_NOTA_MEMO_NOT_SUPPORTED_ERROR,
    EMBEDDED_FILE_MAX_COUNT_ERROR,
    EMBEDDED_FILE_TOTAL_SIZE_ERROR,
    EMBEDDED_FILE_INDIVIDUAL_SIZE_ERROR,
    EMBEDDED_FILE_NOT_FOUND_ERROR,
    EMBEDDED_FILE_UPLOAD_SUCCESS,
    EMBEDDED_FILE_DELETE_SUCCESS,
)
from shared.exceptions import (
    DocumentNotFoundError,
    DocumentStateError,
    AuthorizationError,
    ValidationError,
)
from services.shared.file_validation import validate_embedded_file, sanitize_embedded_file_name
from services.r2_client import r2_put, r2_delete, r2_get_object, r2_signed_url
from shared.logging import get_logger

logger = get_logger(__name__)

_EMBEDDED_FILES_DOWNLOAD_TTL = 1800


def _unique_file_name(desired_name: str, existing_names: List[str]) -> str:
    if desired_name not in existing_names:
        return desired_name

    if "." in desired_name:
        stem, ext = desired_name.rsplit(".", 1)
        ext = f".{ext}"
    else:
        stem, ext = desired_name, ""

    n = 1
    while True:
        candidate = f"{stem} ({n}){ext}"
        if candidate not in existing_names:
            return candidate
        n += 1


async def upload_embedded_file(
    document_id: str,
    user_id: str,
    original_filename: str,
    content: bytes,
    *,
    schema_name: str,
) -> Dict[str, Any]:
    size = len(content)

    if size > MAX_EMBEDDED_FILE_SIZE:
        raise ValidationError(
            EMBEDDED_FILE_INDIVIDUAL_SIZE_ERROR.format(max_mb=MAX_EMBEDDED_FILE_SIZE // (1024 * 1024))
        )

    extension = validate_embedded_file(content, original_filename)
    safe_name_candidate = sanitize_embedded_file_name(original_filename)

    file_id = str(uuid.uuid4())
    r2_key: Optional[str] = None
    final_name: Optional[str] = None

    async with transaction(schema_name=schema_name) as conn:
        doc_row = await conn.fetchrow(
            """
            SELECT d.id, d.status, d.created_by, d.document_type_id,
                   dt.accepts_embedded_files, dt.type AS document_type_source
            FROM document_draft d
            JOIN document_types dt ON dt.id = d.document_type_id
            WHERE d.id = $1
            FOR UPDATE OF d
            """,
            document_id,
        )
        if not doc_row:
            raise DocumentNotFoundError(document_id)

        if doc_row["status"] not in EDITABLE_DOCUMENT_STATES:
            raise DocumentStateError(
                EMBEDDED_FILE_NOT_EDITABLE_ERROR,
                current_state=doc_row["status"],
                required_state=" o ".join(EDITABLE_DOCUMENT_STATES),
            )

        if str(doc_row["created_by"]) != str(user_id):
            raise AuthorizationError(EMBEDDED_FILE_NOT_CREATOR_ERROR)

        if not doc_row["accepts_embedded_files"]:
            raise ValidationError(EMBEDDED_FILE_TYPE_NOT_ALLOWED_ERROR)

        if (doc_row["document_type_source"] or "").upper() in ("NOTA", "MEMO"):
            raise ValidationError(EMBEDDED_FILE_NOTA_MEMO_NOT_SUPPORTED_ERROR)

        existing_rows = await conn.fetch(
            "SELECT file_name, file_size FROM document_draft_embedded_files WHERE document_id = $1",
            document_id,
        )
        existing_names = [r["file_name"] for r in existing_rows]
        current_total_size = sum(r["file_size"] for r in existing_rows)

        if len(existing_rows) >= MAX_EMBEDDED_FILES_PER_DOCUMENT:
            raise ValidationError(
                EMBEDDED_FILE_MAX_COUNT_ERROR.format(max_count=MAX_EMBEDDED_FILES_PER_DOCUMENT)
            )

        if current_total_size + size > MAX_TOTAL_EMBEDDED_SIZE:
            raise ValidationError(
                EMBEDDED_FILE_TOTAL_SIZE_ERROR.format(max_mb=MAX_TOTAL_EMBEDDED_SIZE // (1024 * 1024))
            )

        final_name = _unique_file_name(safe_name_candidate, existing_names)
        r2_key = f"editing/{document_id}/{file_id}/{final_name}"

        await conn.execute(
            """
            INSERT INTO document_draft_embedded_files
                (id, document_id, r2_key, file_name, file_size, extension, created_by)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            """,
            file_id, document_id, r2_key, final_name, size, extension, user_id,
        )

    try:
        await r2_put(schema_name=schema_name, key=r2_key, body=content, content_type="application/octet-stream")
    except Exception as e:
        logger.error(f"upload_embedded_file: r2_put falló para {file_id}, revirtiendo fila: {e}")
        await execute(
            "DELETE FROM document_draft_embedded_files WHERE id = $1",
            file_id,
            schema_name=schema_name,
        )
        raise ValidationError(f"No se pudo subir el archivo adjunto: {e}")

    logger.info(f"Adjunto embebido subido: documento={document_id[:8]}... file_id={file_id[:8]}... ({size} bytes)")

    return {
        "success": True,
        "message": EMBEDDED_FILE_UPLOAD_SUCCESS,
        "file_id": file_id,
        "file_name": final_name,
        "file_size": size,
        "extension": extension,
    }


async def _assert_can_view_document_or_403(document_id: str, user_id: str, *, schema_name: str) -> None:
    from services.documents.permissions import can_user_view_document

    can_view = await can_user_view_document(document_id, user_id, schema_name=schema_name)
    if not can_view:
        raise AuthorizationError("Usuario no tiene permisos para ver este documento")


async def list_embedded_files(document_id: str, user_id: str, *, schema_name: str) -> List[Dict[str, Any]]:
    await _assert_can_view_document_or_403(document_id, user_id, schema_name=schema_name)

    rows = await fetch_all(
        """
        SELECT id, file_name, file_size, extension, created_at
        FROM document_draft_embedded_files
        WHERE document_id = $1
        ORDER BY created_at ASC
        """,
        document_id,
        schema_name=schema_name,
    )
    return [
        {
            "file_id": str(r["id"]),
            "file_name": r["file_name"],
            "file_size": r["file_size"],
            "extension": r["extension"],
            "created_at": r["created_at"].isoformat() if r["created_at"] else None,
        }
        for r in rows
    ]


async def get_embedded_file_download_url(document_id: str, file_id: str, user_id: str, *, schema_name: str) -> Dict[str, Any]:
    await _assert_can_view_document_or_403(document_id, user_id, schema_name=schema_name)

    row = await fetch_one(
        "SELECT r2_key, file_name FROM document_draft_embedded_files WHERE id = $1 AND document_id = $2",
        file_id, document_id,
        schema_name=schema_name,
    )
    if not row:
        raise DocumentNotFoundError(EMBEDDED_FILE_NOT_FOUND_ERROR)

    url = await r2_signed_url(schema_name=schema_name, key=row["r2_key"], ttl=_EMBEDDED_FILES_DOWNLOAD_TTL)
    return {"url": url, "file_name": row["file_name"], "ttl": _EMBEDDED_FILES_DOWNLOAD_TTL}


async def delete_embedded_file(document_id: str, file_id: str, user_id: str, *, schema_name: str) -> Dict[str, Any]:
    r2_key: Optional[str] = None

    async with transaction(schema_name=schema_name) as conn:
        doc_row = await conn.fetchrow(
            "SELECT id, status, created_by FROM document_draft WHERE id = $1 FOR UPDATE",
            document_id,
        )
        if not doc_row:
            raise DocumentNotFoundError(document_id)

        if doc_row["status"] not in EDITABLE_DOCUMENT_STATES:
            raise DocumentStateError(
                EMBEDDED_FILE_NOT_EDITABLE_ERROR,
                current_state=doc_row["status"],
                required_state=" o ".join(EDITABLE_DOCUMENT_STATES),
            )

        if str(doc_row["created_by"]) != str(user_id):
            raise AuthorizationError(EMBEDDED_FILE_NOT_CREATOR_ERROR)

        file_row = await conn.fetchrow(
            "SELECT r2_key FROM document_draft_embedded_files WHERE id = $1 AND document_id = $2",
            file_id, document_id,
        )
        if not file_row:
            raise DocumentNotFoundError(EMBEDDED_FILE_NOT_FOUND_ERROR)
        r2_key = file_row["r2_key"]

        await conn.execute(
            "DELETE FROM document_draft_embedded_files WHERE id = $1",
            file_id,
        )

    try:
        await r2_delete(schema_name=schema_name, key=r2_key)
    except Exception as e:
        logger.warning(f"delete_embedded_file: error borrando objeto R2 {r2_key} (soft-fail): {e}")

    logger.info(f"Adjunto embebido eliminado: documento={document_id[:8]}... file_id={file_id[:8]}...")

    return {"success": True, "message": EMBEDDED_FILE_DELETE_SUCCESS, "file_id": file_id}


async def upload_embedded_files_for_citizen_document(
    document_id: str,
    citizen_id: str,
    files: List[Tuple[str, bytes]],
    *,
    schema_name: str,
) -> List[Dict[str, Any]]:
    if not files:
        return []

    doc_row = await fetch_one(
        """
        SELECT d.id, d.created_by_citizen, dt.accepts_embedded_files
        FROM document_draft d
        JOIN document_types dt ON dt.id = d.document_type_id
        WHERE d.id = $1
        """,
        document_id,
        schema_name=schema_name,
    )
    if not doc_row:
        raise DocumentNotFoundError(document_id)

    if str(doc_row["created_by_citizen"]) != str(citizen_id):
        raise AuthorizationError(EMBEDDED_FILE_NOT_CREATOR_ERROR)

    if not doc_row["accepts_embedded_files"]:
        raise ValidationError(EMBEDDED_FILE_TYPE_NOT_ALLOWED_ERROR)

    if len(files) > MAX_EMBEDDED_FILES_PER_DOCUMENT:
        raise ValidationError(
            EMBEDDED_FILE_MAX_COUNT_ERROR.format(max_count=MAX_EMBEDDED_FILES_PER_DOCUMENT)
        )

    total_size = sum(len(content) for _, content in files)
    if total_size > MAX_TOTAL_EMBEDDED_SIZE:
        raise ValidationError(
            EMBEDDED_FILE_TOTAL_SIZE_ERROR.format(max_mb=MAX_TOTAL_EMBEDDED_SIZE // (1024 * 1024))
        )

    existing_names: List[str] = []
    inserted: List[Dict[str, Any]] = []

    for original_filename, content in files:
        size = len(content)
        if size > MAX_EMBEDDED_FILE_SIZE:
            raise ValidationError(
                EMBEDDED_FILE_INDIVIDUAL_SIZE_ERROR.format(max_mb=MAX_EMBEDDED_FILE_SIZE // (1024 * 1024))
            )

        extension = validate_embedded_file(content, original_filename)
        safe_name_candidate = sanitize_embedded_file_name(original_filename)
        final_name = _unique_file_name(safe_name_candidate, existing_names)
        existing_names.append(final_name)

        file_id = str(uuid.uuid4())
        r2_key = f"editing/{document_id}/{file_id}/{final_name}"

        await execute(
            """
            INSERT INTO document_draft_embedded_files
                (id, document_id, r2_key, file_name, file_size, extension, created_by_citizen)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            """,
            file_id, document_id, r2_key, final_name, size, extension, citizen_id,
            schema_name=schema_name,
        )

        try:
            await r2_put(schema_name=schema_name, key=r2_key, body=content, content_type="application/octet-stream")
        except Exception as e:
            logger.error(
                f"upload_embedded_files_for_citizen_document: r2_put falló para "
                f"{file_id} ({original_filename}), revirtiendo fila: {e}"
            )
            await execute(
                "DELETE FROM document_draft_embedded_files WHERE id = $1",
                file_id,
                schema_name=schema_name,
            )
            raise ValidationError(f"No se pudo subir el archivo adjunto '{original_filename}': {e}")

        inserted.append({
            "file_id": file_id,
            "file_name": final_name,
            "file_size": size,
            "extension": extension,
        })

    logger.info(
        f"[TAD] {len(inserted)} adjunto(s) embebido(s) cargado(s) para documento "
        f"citizen {document_id[:8]}..."
    )
    return inserted


async def fetch_embedded_files_for_signing(document_id: str, *, schema_name: str) -> List[Tuple[str, bytes]]:
    import asyncio
    from shared.exceptions import ExternalServiceError
    from config.constants import EMBEDDED_FILE_EMBED_ERROR

    rows = await fetch_all(
        "SELECT id, r2_key, file_name FROM document_draft_embedded_files WHERE document_id = $1 ORDER BY created_at ASC",
        document_id,
        schema_name=schema_name,
    )
    if not rows:
        return []

    result: List[Tuple[str, bytes]] = []
    for row in rows:
        file_id = str(row["id"])
        r2_key = row["r2_key"]
        file_name = row["file_name"]

        last_error: Optional[Exception] = None
        for attempt in range(3):
            try:
                content = await r2_get_object(schema_name=schema_name, key=r2_key)
                result.append((file_name, content))
                break
            except Exception as e:
                last_error = e
                logger.warning(
                    f"fetch_embedded_files_for_signing: intento {attempt + 1}/3 falló "
                    f"para adjunto {file_id[:8]}... (documento {document_id[:8]}...): {e}"
                )
                if attempt < 2:
                    await asyncio.sleep(1 * (attempt + 1))
        else:
            logger.error(
                f"fetch_embedded_files_for_signing: adjunto {file_id[:8]}... irrecuperable "
                f"tras 3 intentos, documento {document_id[:8]}...: {last_error}"
            )
            raise ExternalServiceError(EMBEDDED_FILE_EMBED_ERROR)

    return result


async def promote_embedded_files_to_official(
    document_id: str, official_document_id: str, *, schema_name: str
) -> None:
    rows = await fetch_all(
        "SELECT id, r2_key, file_name, file_size, extension, created_by, created_by_citizen, created_at "
        "FROM document_draft_embedded_files WHERE document_id = $1",
        document_id,
        schema_name=schema_name,
    )
    if not rows:
        return

    try:
        async with transaction(schema_name=schema_name) as conn:
            for row in rows:
                await conn.execute(
                    """
                    INSERT INTO official_document_embedded_files
                        (id, official_document_id, file_name, file_size, extension, created_by, created_by_citizen, created_at)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                    """,
                    str(uuid.uuid4()), official_document_id,
                    row["file_name"], row["file_size"], row["extension"],
                    row["created_by"], row["created_by_citizen"], row["created_at"],
                )
    except Exception as e:
        logger.error(
            f"promote_embedded_files_to_official: FALLÓ el INSERT de metadata "
            f"({len(rows)} fila(s)) para documento {document_id} — el visor oficial "
            "NO mostrará estos adjuntos aunque viajen embebidos en el PDF firmado. "
            f"Requiere revisión manual/reconciliación. Error: {e}"
        )
        return

    try:
        await execute(
            "DELETE FROM document_draft_embedded_files WHERE document_id = $1",
            document_id,
            schema_name=schema_name,
        )
    except Exception as e:
        logger.error(
            f"promote_embedded_files_to_official: no se pudieron borrar "
            f"{len(rows)} fila(s) draft para documento {document_id} (residuo, "
            f"metadata oficial YA insertada, no bloquea): {e}"
        )

    for row in rows:
        try:
            await r2_delete(schema_name=schema_name, key=row["r2_key"])
        except Exception as e:
            logger.error(
                f"promote_embedded_files_to_official: error borrando {row['r2_key']} "
                f"para documento {document_id} (soft-fail, metadata oficial YA insertada): {e}"
            )

    logger.info(
        f"promote_embedded_files_to_official: {len(rows)} adjunto(s) promovido(s) a oficial "
        f"para documento {document_id}"
    )


async def purge_document_embedded_files(document_id: str, *, schema_name: str) -> None:
    try:
        rows = await fetch_all(
            "SELECT r2_key FROM document_draft_embedded_files WHERE document_id = $1",
            document_id,
            schema_name=schema_name,
        )
        if not rows:
            return

        for row in rows:
            try:
                await r2_delete(schema_name=schema_name, key=row["r2_key"])
            except Exception as e:
                logger.warning(f"purge_document_embedded_files: error borrando {row['r2_key']} (soft-fail): {e}")

        await execute(
            "DELETE FROM document_draft_embedded_files WHERE document_id = $1",
            document_id,
            schema_name=schema_name,
        )
        logger.info(f"purge_document_embedded_files: {len(rows)} adjunto(s) purgado(s) para documento {document_id[:8]}...")
    except Exception as e:
        logger.warning(f"purge_document_embedded_files: fallo (soft-fail) documento {document_id[:8]}...: {e}")


async def get_official_embedded_files(document_id: str, user_id: str, *, schema_name: str) -> List[Dict[str, Any]]:
    from services.documents.permissions import can_user_view_document

    can_view = await can_user_view_document(document_id, user_id, schema_name=schema_name)
    if not can_view:
        raise AuthorizationError("Usuario no tiene permisos para ver este documento")

    rows = await fetch_all(
        """
        SELECT file_name, file_size, extension
        FROM official_document_embedded_files
        WHERE official_document_id = $1
        ORDER BY created_at ASC
        """,
        document_id,
        schema_name=schema_name,
    )
    return [
        {"file_name": r["file_name"], "file_size": r["file_size"], "extension": r["extension"]}
        for r in rows
    ]
