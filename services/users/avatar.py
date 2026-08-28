
import io
import uuid
from typing import Optional

from PIL import Image, ImageOps

from database import fetch_one
from shared.exceptions import ValidationError
from shared.validation import detect_image_mime
from shared.logging import get_logger

logger = get_logger(__name__)

MAX_AVATAR_BYTES = 2 * 1024 * 1024
AVATAR_SIZE_PX = 512

Image.MAX_IMAGE_PIXELS = 20_000_000


def _resize_square_webp(image_bytes: bytes) -> bytes:
    with Image.open(io.BytesIO(image_bytes)) as img:
        img = ImageOps.exif_transpose(img)
        img = img.convert("RGB")
        width, height = img.size
        side = min(width, height)
        left = (width - side) // 2
        top = (height - side) // 2
        img = img.crop((left, top, left + side, top + side))
        img = img.resize((AVATAR_SIZE_PX, AVATAR_SIZE_PX), Image.LANCZOS)

        buffer = io.BytesIO()
        img.save(buffer, format="WEBP", quality=85)
        return buffer.getvalue()


def _extract_avatar_key(url: Optional[str]) -> Optional[str]:
    import os

    public_base = os.getenv('CF_R2_AVATARS_PUBLIC_URL')
    if not url or not public_base:
        return None
    public_base = public_base.rstrip('/')
    if not url.startswith(public_base + '/'):
        return None
    return url[len(public_base) + 1:]


async def upload_profile_picture(user_id: str, image_bytes: bytes, *, schema_name: str) -> str:
    from services.storage.cloudflare import upload_avatar, delete_avatar
    from services.users.profile import update_user_profile

    if len(image_bytes) > MAX_AVATAR_BYTES:
        raise ValidationError(f"La imagen supera el tamaño máximo permitido ({MAX_AVATAR_BYTES // (1024*1024)}MB)")

    mime_type = detect_image_mime(image_bytes)
    if not mime_type:
        raise ValidationError("Formato de imagen no soportado (solo PNG, JPEG o WEBP)")

    try:
        processed_bytes = _resize_square_webp(image_bytes)
    except Image.DecompressionBombError as e:
        logger.warning(f"Decompression bomb detectada en avatar de usuario {user_id}: {e}")
        raise ValidationError("Imagen demasiado grande (dimensiones excesivas)")
    except Exception as e:
        logger.error(f"Error procesando avatar de usuario {user_id}: {type(e).__name__} - {e}")
        raise ValidationError("No se pudo procesar la imagen (archivo corrupto o formato inválido)")

    current = await fetch_one(
        "SELECT profile_picture_url FROM users WHERE id = $1", user_id, schema_name=schema_name
    )
    old_url = current["profile_picture_url"] if current else None

    new_key = f"{schema_name}/{uuid.uuid4()}.webp"
    new_url = upload_avatar(processed_bytes, new_key)

    await update_user_profile(user_id=user_id, profile_picture_url=new_url, schema_name=schema_name)

    old_key = _extract_avatar_key(old_url)
    if old_key:
        try:
            delete_avatar(old_key)
        except Exception as e:
            logger.warning(f"No se pudo borrar avatar anterior {old_key} (soft-fail): {e}")

    logger.info(f"Avatar actualizado para usuario {user_id[:8]}...")
    return new_url
