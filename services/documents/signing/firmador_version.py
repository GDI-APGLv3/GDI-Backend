
from config.constants import FIRMADOR_VERSION_MINIMA
from database import fetch_one
from shared.logging import get_logger

log = get_logger(__name__)

URL_DESCARGA = "https://firmadorgdi.gdilatam.com/FirmadorGDI-latest.msi"


def _como_numeros(version: str) -> tuple[int, ...]:
    partes = []
    for trozo in (version or "").split("."):
        try:
            partes.append(int(trozo))
        except ValueError:
            return ()
    return tuple(partes)


def esta_vieja(version: str | None, *, minima: str | None = None) -> bool:
    minima = minima or FIRMADOR_VERSION_MINIMA

    if not version:
        return True

    actual = _como_numeros(version)
    piso = _como_numeros(minima)
    if not actual or not piso:
        return True

    return actual < piso


async def ultima_version_del_usuario(user_id: str) -> str | None:
    try:
        fila = await fetch_one(
            """
            SELECT client_version
            FROM public.digital_signature_sessions
            WHERE user_id = $1 AND client_version IS NOT NULL
            ORDER BY created_at DESC
            LIMIT 1
            """,
            str(user_id),
            schema_name="public",
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("firmador_version.consulta_fallida user=%s: %s", str(user_id)[:8], exc)
        return None

    return fila["client_version"] if fila else None


def aviso_de_actualizacion(version: str | None) -> dict | None:
    if not esta_vieja(version):
        return None

    return {
        "version_actual": version,
        "version_minima": FIRMADOR_VERSION_MINIMA,
        "url_descarga": URL_DESCARGA,
        "mensaje": (
            f"Tu FirmadorGDI quedó desactualizado (necesitás la "
            f"{FIRMADOR_VERSION_MINIMA} o posterior). Descargá la última versión "
            f"e instalala: reemplaza la que tenés, no hace falta desinstalar nada."
        ),
    }


async def aviso_para_usuario(user_id: str) -> dict | None:
    version = await ultima_version_del_usuario(user_id)
    if version is None:
        return None
    return aviso_de_actualizacion(version)
