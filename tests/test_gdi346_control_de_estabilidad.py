import asyncio

import pytest
from _pytest.outcomes import Skipped as _Skipped

from tests._home_parity_helpers import leer_estable


def _lector(*resultados):
    pendientes = list(resultados)

    async def leer(paralelo):  # noqa: ARG001 — el orden es lo que importa acá
        if not pendientes:
            raise AssertionError("se leyó más veces de las esperadas")
        valor = pendientes.pop(0)
        if isinstance(valor, BaseException):
            raise valor
        return valor

    return leer, pendientes


class TestElBugRealSigueSiendoRojo:

    @pytest.mark.asyncio
    async def test_si_los_caminos_diferen_y_la_base_esta_quieta_NO_skipea(self):
        leer, _ = _lector({"total": 1}, {"total": 999}, {"total": 1})

        secuencial, paralelo = await leer_estable(leer, etiqueta="bug real")

        assert secuencial == {"total": 1}
        assert paralelo == {"total": 999}
        assert secuencial != paralelo, "el test de arriba fallaría acá, que es lo correcto"

    @pytest.mark.asyncio
    async def test_hace_la_tercera_lectura_solo_cuando_hay_discrepancia(self):
        leer, pendientes = _lector({"total": 7}, {"total": 7}, {"total": "NO SE DEBE LEER"})

        secuencial, paralelo = await leer_estable(leer)

        assert secuencial == paralelo == {"total": 7}
        assert len(pendientes) == 1, "leyó una tercera vez sin necesidad"


class TestLoQueSiDebeSkipear:

    @pytest.mark.asyncio
    async def test_la_base_se_movio_entre_lecturas(self):
        leer, _ = _lector({"total": 1}, {"total": 2}, {"total": 3})

        with pytest.raises(_Skipped) as exc:
            await leer_estable(leer, etiqueta="base movida")

        assert "GDI-346" in str(exc.value)

    @pytest.mark.asyncio
    async def test_falla_de_conexion_en_la_primera_lectura(self):
        leer, _ = _lector(asyncio.TimeoutError())

        with pytest.raises(_Skipped) as exc:
            await leer_estable(leer, etiqueta="tunel caido")

        assert "GDI-346" in str(exc.value)

    @pytest.mark.asyncio
    async def test_el_cancelled_del_pool_tambien_skipea(self):
        leer, _ = _lector({"total": 1}, asyncio.CancelledError())

        with pytest.raises(_Skipped) as exc:
            await leer_estable(leer)

        assert "GDI-346" in str(exc.value)


class TestLoQueNOSeDebeTapar:

    @pytest.mark.asyncio
    async def test_un_error_del_codigo_se_propaga_tal_cual(self):
        leer, _ = _lector(ValueError("bug en get_home_count"))

        with pytest.raises(ValueError, match="bug en get_home_count"):
            await leer_estable(leer)

    @pytest.mark.asyncio
    async def test_un_keyerror_tampoco_se_convierte_en_skip(self):
        leer, _ = _lector({"total": 1}, KeyError("scope"))

        with pytest.raises(KeyError):
            await leer_estable(leer)


class TestLaComparacionPropia:

    @pytest.mark.asyncio
    async def test_respeta_la_funcion_iguales_que_le_pasan(self):
        leer, pendientes = _lector(
            {"scope": "mine", "ruido": 1},
            {"scope": "mine", "ruido": 2},
            {"scope": "NO SE DEBE LEER"},
        )

        solo_scope = lambda a, b: a["scope"] == b["scope"]
        secuencial, paralelo = await leer_estable(leer, iguales=solo_scope)

        assert secuencial["scope"] == paralelo["scope"]
        assert len(pendientes) == 1, "no respetó `iguales` y pagó la tercera lectura"
