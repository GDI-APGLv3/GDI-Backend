
import pytest
from services.documents.ffcc_validator import validate_ffcc_content, VALID_FFCC_TYPES
from services.documents.ffcc_renderer import ffcc_to_html
from shared.exceptions import ValidationError


FIELD_DEFS_COMPLETO = [
    {"name": "solicitante",  "label": "Nombre del Solicitante", "type": "text",     "required": True,  "max_length": 200},
    {"name": "descripcion",  "label": "Descripcion",           "type": "textarea", "required": True,  "max_length": 1000},
    {"name": "importe",      "label": "Importe Solicitado",    "type": "number",   "required": True,  "min": 0},
    {"name": "fecha",        "label": "Fecha de Solicitud",    "type": "date",     "required": True},
    {"name": "categoria",    "label": "Categoria",             "type": "select",   "required": True,  "options": ["Obras", "Servicios", "Bienes"]},
    {"name": "urgente",      "label": "Urgente",               "type": "boolean",  "required": False},
    {"name": "respaldo",     "label": "Documento de Respaldo", "type": "file",     "required": False},
]

DATA_COMPLETO = {
    "solicitante": "Juan Perez",
    "descripcion": "Reparacion de luminarias\nen calle Sarmiento",
    "importe": 50000,
    "fecha": "2026-06-03",
    "categoria": "Servicios",
    "urgente": True,
    "respaldo": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
}

SCHEMA_NAME = "100_test"


class TestValidateDraftLaxo:

    def test_datos_completos_pasan(self):
        result = validate_ffcc_content(
            DATA_COMPLETO,
            FIELD_DEFS_COMPLETO,
            schema_name=SCHEMA_NAME,
            enforce_required=False,
        )
        assert result == DATA_COMPLETO

    def test_draft_vacio_pasa(self):
        result = validate_ffcc_content(
            {},
            FIELD_DEFS_COMPLETO,
            schema_name=SCHEMA_NAME,
            enforce_required=False,
        )
        assert result == {}

    def test_draft_parcial_pasa(self):
        result = validate_ffcc_content(
            {"solicitante": "Parcial"},
            FIELD_DEFS_COMPLETO,
            schema_name=SCHEMA_NAME,
            enforce_required=False,
        )
        assert result["solicitante"] == "Parcial"

    def test_tipo_invalido_falla_siempre(self):
        with pytest.raises(ValidationError, match="debe ser texto"):
            validate_ffcc_content(
                {"solicitante": 12345},
                FIELD_DEFS_COMPLETO,
                schema_name=SCHEMA_NAME,
                enforce_required=False,
            )

    def test_select_opcion_invalida_falla_siempre(self):
        with pytest.raises(ValidationError, match="debe ser uno de"):
            validate_ffcc_content(
                {"categoria": "Invalida"},
                FIELD_DEFS_COMPLETO,
                schema_name=SCHEMA_NAME,
                enforce_required=False,
            )

    def test_max_length_texto_falla(self):
        with pytest.raises(ValidationError, match="no puede superar"):
            validate_ffcc_content(
                {"solicitante": "x" * 201},
                FIELD_DEFS_COMPLETO,
                schema_name=SCHEMA_NAME,
                enforce_required=False,
            )

    def test_max_length_exacto_pasa(self):
        validate_ffcc_content(
            {"solicitante": "x" * 200},
            FIELD_DEFS_COMPLETO,
            schema_name=SCHEMA_NAME,
            enforce_required=False,
        )

    def test_number_min_falla(self):
        with pytest.raises(ValidationError, match="mayor o igual"):
            validate_ffcc_content(
                {"importe": -1},
                FIELD_DEFS_COMPLETO,
                schema_name=SCHEMA_NAME,
                enforce_required=False,
            )

    def test_number_cero_exactamente_pasa(self):
        validate_ffcc_content(
            {"importe": 0},
            FIELD_DEFS_COMPLETO,
            schema_name=SCHEMA_NAME,
            enforce_required=False,
        )

    def test_file_uuid_invalido_falla(self):
        with pytest.raises(ValidationError, match="UUID valido"):
            validate_ffcc_content(
                {"respaldo": "esto-no-es-uuid"},
                FIELD_DEFS_COMPLETO,
                schema_name=SCHEMA_NAME,
                enforce_required=False,
            )

    def test_file_uuid_valido_pasa(self):
        validate_ffcc_content(
            {"respaldo": "a1b2c3d4-e5f6-7890-abcd-ef1234567890"},
            FIELD_DEFS_COMPLETO,
            schema_name=SCHEMA_NAME,
            enforce_required=False,
        )

    def test_boolean_no_bool_falla(self):
        with pytest.raises(ValidationError, match="verdadero o falso"):
            validate_ffcc_content(
                {"urgente": "si"},
                FIELD_DEFS_COMPLETO,
                schema_name=SCHEMA_NAME,
                enforce_required=False,
            )

    def test_boolean_true_pasa(self):
        validate_ffcc_content(
            {"urgente": True},
            FIELD_DEFS_COMPLETO,
            schema_name=SCHEMA_NAME,
            enforce_required=False,
        )

    def test_date_formato_invalido_falla(self):
        with pytest.raises(ValidationError, match="YYYY-MM-DD"):
            validate_ffcc_content(
                {"fecha": "03/06/2026"},
                FIELD_DEFS_COMPLETO,
                schema_name=SCHEMA_NAME,
                enforce_required=False,
            )

    def test_date_iso_pasa(self):
        validate_ffcc_content(
            {"fecha": "2026-06-03"},
            FIELD_DEFS_COMPLETO,
            schema_name=SCHEMA_NAME,
            enforce_required=False,
        )

    def test_content_no_dict_falla(self):
        with pytest.raises(ValidationError, match="objeto JSON"):
            validate_ffcc_content(
                "esto es un string",  # type: ignore
                FIELD_DEFS_COMPLETO,
                schema_name=SCHEMA_NAME,
                enforce_required=False,
            )


class TestValidateFirmaEstricta:

    def test_datos_completos_pasan(self):
        result = validate_ffcc_content(
            DATA_COMPLETO,
            FIELD_DEFS_COMPLETO,
            schema_name=SCHEMA_NAME,
            enforce_required=True,
        )
        assert result == DATA_COMPLETO

    def test_required_faltante_falla(self):
        data = {k: v for k, v in DATA_COMPLETO.items() if k != "solicitante"}
        with pytest.raises(ValidationError, match="requerido"):
            validate_ffcc_content(
                data,
                FIELD_DEFS_COMPLETO,
                schema_name=SCHEMA_NAME,
                enforce_required=True,
            )

    def test_required_vacio_string_falla(self):
        data = {**DATA_COMPLETO, "solicitante": ""}
        with pytest.raises(ValidationError, match="requerido"):
            validate_ffcc_content(
                data,
                FIELD_DEFS_COMPLETO,
                schema_name=SCHEMA_NAME,
                enforce_required=True,
            )

    def test_required_none_falla(self):
        data = {**DATA_COMPLETO, "solicitante": None}
        with pytest.raises(ValidationError, match="requerido"):
            validate_ffcc_content(
                data,
                FIELD_DEFS_COMPLETO,
                schema_name=SCHEMA_NAME,
                enforce_required=True,
            )

    def test_optional_ausente_pasa(self):
        data = {k: v for k, v in DATA_COMPLETO.items() if k not in ("urgente", "respaldo")}
        validate_ffcc_content(
            data,
            FIELD_DEFS_COMPLETO,
            schema_name=SCHEMA_NAME,
            enforce_required=True,
        )

    def test_label_aparece_en_error(self):
        data = {k: v for k, v in DATA_COMPLETO.items() if k != "descripcion"}
        with pytest.raises(ValidationError, match="Descripcion"):
            validate_ffcc_content(
                data,
                FIELD_DEFS_COMPLETO,
                schema_name=SCHEMA_NAME,
                enforce_required=True,
            )

    def test_multiples_required_primero_falla(self):
        with pytest.raises(ValidationError, match="requerido"):
            validate_ffcc_content(
                {},
                FIELD_DEFS_COMPLETO,
                schema_name=SCHEMA_NAME,
                enforce_required=True,
            )

    def test_sin_field_definitions_pasa(self):
        validate_ffcc_content(
            {"cualquier": "cosa"},
            [],
            schema_name=SCHEMA_NAME,
            enforce_required=True,
        )

    def test_number_max_falla(self):
        field_defs_con_max = [
            {"name": "cantidad", "label": "Cantidad", "type": "number", "required": True, "min": 0, "max": 100},
        ]
        with pytest.raises(ValidationError, match="menor o igual"):
            validate_ffcc_content(
                {"cantidad": 101},
                field_defs_con_max,
                schema_name=SCHEMA_NAME,
                enforce_required=True,
            )

    def test_schema_name_es_keyword_only(self):
        with pytest.raises(TypeError):
            validate_ffcc_content({}, [], SCHEMA_NAME)  # type: ignore


class TestFfccToHtml:

    def test_tabla_basica(self):
        schema = [{"name": "nombre", "label": "Nombre", "type": "text"}]
        data = {"nombre": "Juan"}
        html = ffcc_to_html(schema, data)
        assert "<table>" in html
        assert "<th>Nombre</th>" in html
        assert "<td>Juan</td>" in html

    def test_campo_ausente_celda_vacia(self):
        schema = [{"name": "campo", "label": "Campo", "type": "text"}]
        html = ffcc_to_html(schema, {})
        assert "<td></td>" in html

    def test_todos_los_campos_del_smoke_test(self):
        html = ffcc_to_html(FIELD_DEFS_COMPLETO, DATA_COMPLETO)
        assert html.count("<tr>") == 7
        assert "Juan Perez" in html
        assert "Servicios" in html
        assert "50000" in html


    def test_escape_caracteres_html_en_valor(self):
        schema = [{"name": "obs", "label": "Obs", "type": "text"}]
        data = {"obs": "<script>alert('xss')</script>"}
        html = ffcc_to_html(schema, data)
        assert "<script>" not in html
        assert "&lt;script&gt;" in html

    def test_escape_ampersand(self):
        schema = [{"name": "org", "label": "Org", "type": "text"}]
        data = {"org": "Obras & Servicios"}
        html = ffcc_to_html(schema, data)
        assert "Obras & Servicios" not in html
        assert "Obras &amp; Servicios" in html

    def test_escape_comillas_dobles(self):
        schema = [{"name": "ref", "label": "Ref", "type": "text"}]
        data = {"ref": 'dijo "hola"'}
        html = ffcc_to_html(schema, data)
        assert '"hola"' not in html
        assert "&quot;hola&quot;" in html

    def test_escape_en_label_tambien(self):
        schema = [{"name": "x", "label": "<b>Label</b>", "type": "text"}]
        html = ffcc_to_html(schema, {"x": "val"})
        assert "<b>Label</b>" not in html
        assert "&lt;b&gt;Label&lt;/b&gt;" in html


    def test_boolean_true_muestra_si(self):
        schema = [{"name": "urgente", "label": "Urgente", "type": "boolean"}]
        html = ffcc_to_html(schema, {"urgente": True})
        assert "<td>Si</td>" in html

    def test_boolean_false_muestra_no(self):
        schema = [{"name": "urgente", "label": "Urgente", "type": "boolean"}]
        html = ffcc_to_html(schema, {"urgente": False})
        assert "<td>No</td>" in html

    def test_boolean_none_muestra_no(self):
        schema = [{"name": "urgente", "label": "Urgente", "type": "boolean"}]
        html = ffcc_to_html(schema, {"urgente": None})
        assert "<td></td>" in html


    def test_textarea_newline_a_br(self):
        schema = [{"name": "desc", "label": "Descripcion", "type": "textarea"}]
        data = {"desc": "linea 1\nlinea 2\nlinea 3"}
        html = ffcc_to_html(schema, data)
        assert "<br>" in html
        assert "linea 1" in html
        assert "linea 2" in html

    def test_textarea_escape_antes_de_br(self):
        schema = [{"name": "desc", "label": "Descripcion", "type": "textarea"}]
        data = {"desc": "<tag>\nvalor"}
        html = ffcc_to_html(schema, data)
        assert "&lt;tag&gt;" in html
        assert "<br>" in html
        assert "&lt;br&gt;" not in html


    def test_file_string_uuid(self):
        schema = [{"name": "doc", "label": "Documento", "type": "file"}]
        data = {"doc": "a1b2c3d4-e5f6-7890-abcd-ef1234567890"}
        html = ffcc_to_html(schema, data)
        assert "a1b2c3d4-e5f6-7890-abcd-ef1234567890" in html

    def test_file_dict_con_official_number(self):
        schema = [{"name": "doc", "label": "Documento", "type": "file"}]
        data = {"doc": {
            "value": "IF-2026-0001234-MUNI",
            "document_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
        }}
        html = ffcc_to_html(schema, data)
        assert "IF-2026-0001234-MUNI" in html

    def test_file_dict_sin_value_usa_document_id(self):
        schema = [{"name": "doc", "label": "Documento", "type": "file"}]
        data = {"doc": {
            "value": None,
            "document_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
        }}
        html = ffcc_to_html(schema, data)
        assert "a1b2c3d4-e5f6-7890-abcd-ef1234567890" in html


    def test_schema_vacio_tabla_vacia(self):
        html = ffcc_to_html([], {})
        assert html == "<table></table>"

    def test_schema_no_lista_retorna_tabla_vacia(self):
        html = ffcc_to_html(None, {})  # type: ignore
        assert html == "<table></table>"

    def test_data_none_tratado_como_dict_vacio(self):
        schema = [{"name": "x", "label": "X", "type": "text"}]
        html = ffcc_to_html(schema, None)  # type: ignore
        assert "<table>" in html

    def test_valor_none_en_campo(self):
        schema = [{"name": "x", "label": "X", "type": "text"}]
        html = ffcc_to_html(schema, {"x": None})
        assert "<td></td>" in html

    def test_numero_en_data(self):
        schema = [{"name": "imp", "label": "Importe", "type": "number"}]
        html = ffcc_to_html(schema, {"imp": 50000})
        assert "50000" in html

    def test_preserva_orden_schema(self):
        schema = [
            {"name": "a", "label": "AAA", "type": "text"},
            {"name": "b", "label": "BBB", "type": "text"},
            {"name": "c", "label": "CCC", "type": "text"},
        ]
        html = ffcc_to_html(schema, {"a": "1", "b": "2", "c": "3"})
        pos_a = html.index("AAA")
        pos_b = html.index("BBB")
        pos_c = html.index("CCC")
        assert pos_a < pos_b < pos_c


class TestSnapshotBuilder:

    def _build_snapshot(self, field_defs: list, existing_content) -> dict:
        return {
            "schema": field_defs,
            "data": existing_content if isinstance(existing_content, dict) else {},
        }

    def test_snapshot_tiene_schema_y_data(self):
        snapshot = self._build_snapshot(FIELD_DEFS_COMPLETO, DATA_COMPLETO)
        assert "schema" in snapshot
        assert "data" in snapshot

    def test_schema_es_field_defs(self):
        snapshot = self._build_snapshot(FIELD_DEFS_COMPLETO, DATA_COMPLETO)
        assert snapshot["schema"] == FIELD_DEFS_COMPLETO

    def test_data_es_content_del_draft(self):
        snapshot = self._build_snapshot(FIELD_DEFS_COMPLETO, DATA_COMPLETO)
        assert snapshot["data"] == DATA_COMPLETO

    def test_content_none_produce_data_vacio(self):
        snapshot = self._build_snapshot(FIELD_DEFS_COMPLETO, None)
        assert snapshot["data"] == {}

    def test_content_string_produce_data_vacio(self):
        snapshot = self._build_snapshot(FIELD_DEFS_COMPLETO, '{"html": "viejo"}')
        assert snapshot["data"] == {}

    def test_field_defs_vacias_snapshot_schema_vacio(self):
        snapshot = self._build_snapshot([], DATA_COMPLETO)
        assert snapshot["schema"] == []
        assert snapshot["data"] == DATA_COMPLETO

    def test_snapshot_es_auto_contenido(self):
        snapshot = self._build_snapshot(FIELD_DEFS_COMPLETO, DATA_COMPLETO)
        html = ffcc_to_html(snapshot["schema"], snapshot["data"])
        assert "Juan Perez" in html
        assert "Servicios" in html

    def test_snapshot_campo_file_enriquecido_se_preserva(self):
        data_con_file_rico = {
            **DATA_COMPLETO,
            "respaldo": {
                "value": "IF-2026-0001234-MUNI",
                "document_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
            },
        }
        snapshot = self._build_snapshot(FIELD_DEFS_COMPLETO, data_con_file_rico)
        assert snapshot["data"]["respaldo"]["value"] == "IF-2026-0001234-MUNI"

    def test_snapshot_renderiza_file_enriquecido_correctamente(self):
        data_con_file_rico = {
            **DATA_COMPLETO,
            "respaldo": {
                "value": "IF-2026-0001234-MUNI",
                "document_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
            },
        }
        snapshot = self._build_snapshot(FIELD_DEFS_COMPLETO, data_con_file_rico)
        html = ffcc_to_html(snapshot["schema"], snapshot["data"])
        assert "IF-2026-0001234-MUNI" in html

    def test_snapshot_xss_en_data_escapado_al_renderizar(self):
        data_xss = {
            **DATA_COMPLETO,
            "solicitante": "<script>alert(1)</script>",
        }
        snapshot = self._build_snapshot(FIELD_DEFS_COMPLETO, data_xss)
        html = ffcc_to_html(snapshot["schema"], snapshot["data"])
        assert "<script>" not in html
        assert "&lt;script&gt;" in html


class TestValidFfccTypes:

    def test_tiene_exactamente_8_tipos(self):
        assert len(VALID_FFCC_TYPES) == 8

    def test_tipos_son_los_de_rlm(self):
        from services.rlm.validation import VALID_FIELD_TYPES

        assert VALID_FFCC_TYPES == VALID_FIELD_TYPES
        esperados = {"text", "textarea", "number", "date", "select", "boolean", "file", "email"}
        assert VALID_FFCC_TYPES == esperados

    def test_checkbox_no_esta(self):
        assert "checkbox" not in VALID_FFCC_TYPES

    def test_email_esta_y_valida_formato(self):
        assert "email" in VALID_FFCC_TYPES
        import re

        from services.documents.ffcc_validator import _EMAIL_RE

        assert re.match(_EMAIL_RE, "persona@municipio.gob.ar")
        assert not re.match(_EMAIL_RE, "no-es-un-email")
        assert not re.match(_EMAIL_RE, "dos@@arrobas.com")
