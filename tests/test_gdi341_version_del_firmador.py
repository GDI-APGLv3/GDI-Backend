
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.documents.signing import firmador_version as fv


class TestCuandoUnaVersionEstaVieja:

    def test_una_version_anterior_a_la_minima_esta_vieja(self):
        assert fv.esta_vieja("1.2.0", minima="1.3.0")
        assert fv.esta_vieja("0.9.9", minima="1.3.0")
        assert fv.esta_vieja("1.2.99", minima="1.3.0")

    def test_la_minima_y_las_posteriores_estan_al_dia(self):
        assert not fv.esta_vieja("1.3.0", minima="1.3.0")
        assert not fv.esta_vieja("1.3.1", minima="1.3.0")
        assert not fv.esta_vieja("2.0.0", minima="1.3.0")

    def test_no_se_comparan_como_texto(self):
        assert not fv.esta_vieja("1.10.0", minima="1.9.0")
        assert fv.esta_vieja("1.9.0", minima="1.10.0")

    def test_sin_version_cuenta_como_vieja(self):
        assert fv.esta_vieja(None, minima="1.3.0")
        assert fv.esta_vieja("", minima="1.3.0")

    def test_una_version_ilegible_cuenta_como_vieja(self):
        assert fv.esta_vieja("no-soy-una-version", minima="1.3.0")
        assert fv.esta_vieja("1.3", minima="1.3.0")


class TestElAvisoQueVeElFuncionario:

    def test_trae_el_link_y_dice_que_no_hay_que_desinstalar(self):
        aviso = fv.aviso_de_actualizacion("1.2.0")

        assert aviso is not None
        assert aviso["url_descarga"] == fv.URL_DESCARGA
        assert aviso["version_actual"] == "1.2.0"
        assert "desinstalar" in aviso["mensaje"]

    def test_al_dia_no_hay_aviso(self):
        assert fv.aviso_de_actualizacion(fv.FIRMADOR_VERSION_MINIMA) is None

    @pytest.mark.asyncio
    async def test_a_quien_nunca_vimos_firmar_no_se_le_avisa(self):
        with patch.object(fv, "ultima_version_del_usuario", AsyncMock(return_value=None)):
            assert await fv.aviso_para_usuario("u1") is None

    @pytest.mark.asyncio
    async def test_a_quien_vimos_con_una_vieja_si(self):
        with patch.object(fv, "ultima_version_del_usuario", AsyncMock(return_value="1.2.0")):
            aviso = await fv.aviso_para_usuario("u1")
        assert aviso is not None
        assert aviso["version_actual"] == "1.2.0"

    @pytest.mark.asyncio
    async def test_si_la_base_falla_no_se_rompe_la_firma(self):
        with patch.object(fv, "fetch_one", AsyncMock(side_effect=RuntimeError("BD caída"))):
            assert await fv.ultima_version_del_usuario("u1") is None


class TestElServidorAnotaLaVersionQueLlega:

    def _request(self, version: str | None):
        req = MagicMock()
        req.headers = {} if version is None else {"X-FirmadorGDI-Version": version}
        return req

    @pytest.mark.asyncio
    async def test_una_version_valida_se_guarda(self):
        from endpoints.digital_signature import storage as st

        ejecutado = AsyncMock()
        with patch("database.execute", ejecutado):
            await st._registrar_version_del_firmador(self._request("1.3.0"), "SESABC")

        ejecutado.assert_awaited_once()
        assert ejecutado.await_args.args[1] == "1.3.0"

    @pytest.mark.asyncio
    async def test_sin_header_no_se_escribe_nada(self):
        from endpoints.digital_signature import storage as st

        ejecutado = AsyncMock()
        with patch("database.execute", ejecutado):
            await st._registrar_version_del_firmador(self._request(None), "SESABC")

        ejecutado.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_una_version_con_formato_raro_se_descarta(self):
        from endpoints.digital_signature import storage as st

        ejecutado = AsyncMock()
        for basura in ["<script>", "1.3.0; DROP TABLE", "999999.1.1", "", "   "]:
            with patch("database.execute", ejecutado):
                await st._registrar_version_del_firmador(self._request(basura), "SESABC")

        ejecutado.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_si_no_se_puede_anotar_la_firma_sigue(self):
        from endpoints.digital_signature import storage as st

        with patch("database.execute", AsyncMock(side_effect=RuntimeError("boom"))):
            await st._registrar_version_del_firmador(self._request("1.3.0"), "SESABC")


class TestLaVersionMinimaEsCoherenteConElFirmador:

    def test_la_minima_no_puede_ser_mayor_que_la_ultima_publicada(self):
        import pathlib
        import re

        ruta = pathlib.Path(__file__).resolve().parents[2] / "FirmadorGDI" / \
            "internal" / "version" / "version.go"
        if not ruta.exists():
            pytest.skip("el repo del FirmadorGDI no está al lado de este")

        m = re.search(r'const Version = "([0-9.]+)"', ruta.read_text(encoding="utf-8"))
        assert m, "no se encontró la versión en version.go"

        publicada = fv._como_numeros(m.group(1))
        minima = fv._como_numeros(fv.FIRMADOR_VERSION_MINIMA)
        assert minima <= publicada, (
            f"la mínima ({fv.FIRMADOR_VERSION_MINIMA}) pide más que la última "
            f"publicada ({m.group(1)}): nadie podría cumplirla"
        )
