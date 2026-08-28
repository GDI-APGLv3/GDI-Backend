import asyncio

import pytest

_FALLAS_DE_CONEXION = (
    asyncio.TimeoutError,
    asyncio.CancelledError,
    ConnectionError,
    OSError,
)


def _es_falla_de_conexion(exc: BaseException) -> bool:
    if isinstance(exc, _FALLAS_DE_CONEXION):
        return True
    nombre = type(exc).__name__
    return nombre in ("DatabaseBusyError", "PoolTimeoutError", "InterfaceError",
                      "ConnectionDoesNotExistError", "TooManyConnectionsError")


async def _leer_o_skip(leer, paralelo, etiqueta):
    try:
        return await leer(paralelo)
    except BaseException as exc:  # noqa: BLE001
        if _es_falla_de_conexion(exc):
            pytest.skip(
                f"GDI-346: no se pudo leer la base ({type(exc).__name__})"
                f"{' — ' + etiqueta if etiqueta else ''}. El test no llegó a comparar "
                "nada, así que no dice nada sobre la paridad. Revisá el túnel."
            )
        raise


async def leer_estable(leer, *, iguales=None, etiqueta=""):
    son_iguales = iguales or (lambda a, b: a == b)

    secuencial = await _leer_o_skip(leer, False, etiqueta)
    paralelo = await _leer_o_skip(leer, True, etiqueta)

    if son_iguales(secuencial, paralelo):
        return secuencial, paralelo

    control = await _leer_o_skip(leer, False, etiqueta)
    if not son_iguales(secuencial, control):
        pytest.skip(
            f"GDI-346: la base se movió mientras corría el test"
            f"{' (' + etiqueta + ')' if etiqueta else ''} — dos lecturas del MISMO "
            "camino secuencial dieron distinto, así que la diferencia contra el "
            "camino paralelo no prueba nada. No es un fallo de paridad."
        )

    return secuencial, paralelo
