
import socket
from unittest.mock import patch

import pytest

from services.webhooks.tad_notify import (
    DestinoWebhookNoPermitido,
    _validar_destino_webhook,
)


def _resuelve_a(ip: str):
    familia = socket.AF_INET6 if ":" in ip else socket.AF_INET
    return lambda host, port: [(familia, socket.SOCK_STREAM, 6, "", (ip, port))]


class TestDestinosBloqueados:
    @pytest.mark.parametrize("ip,que_es", [
        ("169.254.169.254", "metadata del proveedor"),
        ("127.0.0.1", "loopback"),
        ("10.0.0.5", "red privada"),
        ("192.168.1.1", "LAN"),
        ("172.16.0.1", "red privada clase B"),
        ("0.0.0.0", "ruta por defecto"),
        ("::1", "loopback IPv6"),
        ("fd00::1", "unique local IPv6"),
    ])
    def test_ip_no_publica_se_rechaza(self, ip, que_es):
        with patch("socket.getaddrinfo", side_effect=_resuelve_a(ip)):
            with pytest.raises(DestinoWebhookNoPermitido) as e:
                _validar_destino_webhook("https://parece-publico.gob.ar/hook")
        assert "no publica" in str(e.value), que_es

    @pytest.mark.parametrize("url", [
        "http://<your-backend-app>.internal:8080/x",
        "http://postgres.internal/x",
        "http://algo.local/x",
    ])
    def test_sufijo_interno_se_rechaza_sin_resolver(self, url):
        with patch("socket.getaddrinfo", side_effect=AssertionError("no debio resolver")):
            with pytest.raises(DestinoWebhookNoPermitido) as e:
                _validar_destino_webhook(url)
        assert "host interno" in str(e.value)

    @pytest.mark.parametrize("url", [
        "file:///etc/passwd",
        "ftp://archivos.gob.ar/x",
        "gopher://viejo.gob.ar/x",
    ])
    def test_esquema_no_http_se_rechaza(self, url):
        with pytest.raises(DestinoWebhookNoPermitido) as e:
            _validar_destino_webhook(url)
        assert "esquema no permitido" in str(e.value)

    def test_host_que_no_resuelve_se_rechaza(self):
        with patch("socket.getaddrinfo", side_effect=socket.gaierror("sin registro")):
            with pytest.raises(DestinoWebhookNoPermitido) as e:
                _validar_destino_webhook("https://no-existe.invalid/hook")
        assert "no resuelve" in str(e.value)

    def test_url_sin_host(self):
        with pytest.raises(DestinoWebhookNoPermitido):
            _validar_destino_webhook("https:///solo-path")

    def test_una_sola_ip_interna_alcanza_para_rechazar(self):
        def dos_ips(host, port):
            return [
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("200.1.1.1", port)),
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.7", port)),
            ]
        with patch("socket.getaddrinfo", side_effect=dos_ips):
            with pytest.raises(DestinoWebhookNoPermitido):
                _validar_destino_webhook("https://mixto.gob.ar/hook")


class TestDestinosPermitidos:
    @pytest.mark.parametrize("url", [
        "https://portal.municipio.gob.ar/webhook",
        "http://portal.municipio.gob.ar:8080/webhook",
        "https://portal.municipio.gob.ar/webhook?token=x",
    ])
    def test_destino_publico_pasa(self, url):
        with patch("socket.getaddrinfo", side_effect=_resuelve_a("200.1.1.1")):
            _validar_destino_webhook(url)
