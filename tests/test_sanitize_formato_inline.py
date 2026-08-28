import pytest

from shared.validation import sanitize_html


class TestElFormatoDelPortalSobrevive:

    def test_style_inline_se_conserva(self):
        html = '<p style="text-align: center; margin-top: 2cm">Resuelvo</p>'
        assert 'style="text-align: center; margin-top: 2cm"' in sanitize_html(
            html, permitir_formato_inline=True)

    def test_imagen_pegada_se_conserva(self):
        png = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUg=="
        salida = sanitize_html(f'<img src="{png}" alt="escudo">',
                               permitir_formato_inline=True)
        assert png in salida
        assert 'alt="escudo"' in salida

    @pytest.mark.parametrize("mime", ["png", "jpeg", "webp", "gif"])
    def test_los_cuatro_formatos_de_imagen(self, mime):
        uri = f"data:image/{mime};base64,AAAA"
        assert uri in sanitize_html(f'<img src="{uri}">', permitir_formato_inline=True)


class TestLoQueSigueSinPasar:

    def test_script_sigue_afuera(self):
        salida = sanitize_html('<p>ok</p><script>fetch("/robar")</script>',
                               permitir_formato_inline=True)
        assert "script" not in salida.lower()
        assert "robar" not in salida

    def test_handler_onerror_sigue_afuera(self):
        salida = sanitize_html('<img src="http://x/y.png" onerror="alert(1)">',
                               permitir_formato_inline=True)
        assert "onerror" not in salida.lower()

    def test_javascript_en_href_sigue_afuera(self):
        salida = sanitize_html('<a href="javascript:alert(1)">click</a>',
                               permitir_formato_inline=True)
        assert "javascript:" not in salida.lower()

    def test_data_no_sirve_para_un_documento_entero(self):
        salida = sanitize_html(
            '<a href="data:text/html;base64,PHNjcmlwdD5hbGVydCgxKTwvc2NyaXB0Pg==">ver</a>',
            permitir_formato_inline=True)
        assert "data:text/html" not in salida.lower()

    def test_data_tampoco_en_una_imagen_que_no_es_imagen(self):
        salida = sanitize_html('<img src="data:text/html,<b>x</b>">',
                               permitir_formato_inline=True)
        assert "data:text/html" not in salida.lower()

    def test_style_con_url_se_cae_entero(self):
        salida = sanitize_html(
            '<p style="background: url(https://tracker.ru/pixel.png)">hola</p>',
            permitir_formato_inline=True)
        assert "tracker.ru" not in salida
        assert "hola" in salida, "se cae el atributo, no el contenido"

    @pytest.mark.parametrize("payload", [
        "background: url(data:image/png;base64,AAAA)",
        "@import url(http://x/e.css)",
        "width: expression(alert(1))",
        "background: JavaScript:alert(1)",
    ])
    def test_los_otros_payloads_de_style(self, payload):
        salida = sanitize_html(f'<p style="{payload}">t</p>',
                               permitir_formato_inline=True)
        assert "style=" not in salida.lower()


class TestElEditorInternoNoHeredaNada:

    def test_por_defecto_sigue_sin_style(self):
        assert "style" not in sanitize_html('<p style="color:red">x</p>').lower()

    def test_por_defecto_sigue_sin_data(self):
        assert "data:image" not in sanitize_html(
            '<img src="data:image/png;base64,AAAA">').lower()

    def test_una_llamada_permisiva_no_contamina_la_siguiente(self):
        sanitize_html('<p style="color:red">x</p>', permitir_formato_inline=True)
        assert "style" not in sanitize_html('<p style="color:red">x</p>').lower()
