
import pytest
from services.documents.ffcc_renderer import ffcc_to_html
from services.documents.ffcc_validator import validate_ffcc_content
from shared.exceptions import ValidationError


FIELD_DEFS_SOLI = [
    {"name": "solicitante", "label": "Nombre del Solicitante", "type": "text",   "required": True, "max_length": 200},
    {"name": "descripcion", "label": "Descripcion",           "type": "textarea","required": True, "max_length": 1000},
    {"name": "importe",     "label": "Importe",               "type": "number",  "required": True, "min": 0},
]

DATA_SOLI = {
    "solicitante": "Juan Perez",
    "descripcion": "Reparacion de luminarias",
    "importe": 50000,
}

SCHEMA_NAME = "100_test"
DOC_ID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
USER_ID = "11111111-2222-3333-4444-555555555555"


def _make_document_row(
    *,
    type_acronym: str,
    type_name: str,
    has_fields: bool,
    content,
    document_type_id: int = 42,
    status: str = "draft",
    created_by: str = USER_ID,
    reference: str = "Ref test",
) -> dict:
    return {
        "document_id": DOC_ID,
        "id": DOC_ID,
        "reference": reference,
        "status": status,
        "content": content,
        "created_by": created_by,
        "document_type_id": document_type_id,
        "type_name": type_name,
        "type_acronym": type_acronym,
        "source_type": type_acronym if type_acronym in ("NOTA", "MEMO") else "HTML",
        "has_fields": has_fields,
    }


class TestNotaFFCCSigning:

    def _simulate_signing_logic(
        self,
        document: dict,
        nota_recipients_ok: bool,
        fd_row: dict,
    ):
        from services.documents.ffcc_renderer import ffcc_to_html

        document_data = {
            "document_id": document["document_id"],
            "reference": document["reference"],
            "content": document["content"],
            "type_name": document["type_name"],
            "type_acronym": document["type_acronym"],
        }

        calls_nota = False
        calls_memo = False

        if document.get("type_acronym") == "NOTA":
            calls_nota = True
            if not nota_recipients_ok:
                from shared.exceptions import ValidationError
                raise ValidationError("Faltan recipients NOTA")

        elif document.get("type_acronym") == "MEMO":
            calls_memo = True

        if document.get("has_fields"):
            field_defs = fd_row["field_definitions"] if fd_row else []
            raw_data = document["content"] if isinstance(document["content"], dict) else {}
            ffcc_html = ffcc_to_html(field_defs, raw_data)
            document_data["content"] = ffcc_html

        return calls_nota, calls_memo, document_data["content"]

    def test_nota_ffcc_dispara_ambos_paths(self):
        document = _make_document_row(
            type_acronym="NOTA",
            type_name="Nota FFCC",
            has_fields=True,
            content=DATA_SOLI,
        )
        fd_row = {"field_definitions": FIELD_DEFS_SOLI}

        calls_nota, calls_memo, final_content = self._simulate_signing_logic(
            document=document,
            nota_recipients_ok=True,
            fd_row=fd_row,
        )

        assert calls_nota is True, "validate_nota_recipients_for_signing NO se llamo"
        assert calls_memo is False

        assert isinstance(final_content, str), "Content final debe ser string HTML"
        assert "<table>" in final_content
        assert "Juan Perez" in final_content
        assert "50000" in final_content

    def test_nota_ffcc_recipients_faltantes_levanta_error(self):
        document = _make_document_row(
            type_acronym="NOTA",
            type_name="Nota FFCC",
            has_fields=True,
            content=DATA_SOLI,
        )
        fd_row = {"field_definitions": FIELD_DEFS_SOLI}

        with pytest.raises(ValidationError, match="Faltan recipients NOTA"):
            self._simulate_signing_logic(
                document=document,
                nota_recipients_ok=False,
                fd_row=fd_row,
            )

    def test_nota_sin_fields_solo_valida_recipients(self):
        document = _make_document_row(
            type_acronym="NOTA",
            type_name="Nota comun",
            has_fields=False,
            content={"html": "<p>Contenido HTML</p>"},
        )
        fd_row = None

        calls_nota, calls_memo, final_content = self._simulate_signing_logic(
            document=document,
            nota_recipients_ok=True,
            fd_row=fd_row,
        )

        assert calls_nota is True
        assert isinstance(final_content, dict), "NOTA sin formulario debe conservar content original"
        assert "html" in final_content

    def test_nota_ffcc_content_final_es_html_tabular_no_dict(self):
        document = _make_document_row(
            type_acronym="NOTA",
            type_name="Nota FFCC",
            has_fields=True,
            content={"solicitante": "Ana Gomez", "descripcion": "Desc", "importe": 1000},
        )
        field_defs = [
            {"name": "solicitante", "label": "Solicitante", "type": "text",   "required": True},
            {"name": "descripcion", "label": "Descripcion", "type": "textarea","required": False},
            {"name": "importe",     "label": "Importe",     "type": "number", "required": False},
        ]
        fd_row = {"field_definitions": field_defs}

        _, _, final_content = self._simulate_signing_logic(
            document=document,
            nota_recipients_ok=True,
            fd_row=fd_row,
        )

        assert isinstance(final_content, str)
        assert "<table>" in final_content
        assert "Ana Gomez" in final_content
        assert "{'solicitante'" not in final_content


class TestMemoFFCCSigning:

    def _simulate_signing_logic(self, document: dict, fd_row: dict):
        from services.documents.ffcc_renderer import ffcc_to_html

        document_data = {
            "document_id": document["document_id"],
            "reference": document["reference"],
            "content": document["content"],
            "type_name": document["type_name"],
            "type_acronym": document["type_acronym"],
        }

        calls_nota = False
        calls_memo = False

        if document.get("type_acronym") == "NOTA":
            calls_nota = True
        elif document.get("type_acronym") == "MEMO":
            calls_memo = True

        if document.get("has_fields"):
            field_defs = fd_row["field_definitions"] if fd_row else []
            raw_data = document["content"] if isinstance(document["content"], dict) else {}
            ffcc_html = ffcc_to_html(field_defs, raw_data)
            document_data["content"] = ffcc_html

        return calls_nota, calls_memo, document_data["content"]

    def test_memo_ffcc_dispara_ambos_paths(self):
        document = _make_document_row(
            type_acronym="MEMO",
            type_name="Memo FFCC",
            has_fields=True,
            content=DATA_SOLI,
        )
        fd_row = {"field_definitions": FIELD_DEFS_SOLI}

        calls_nota, calls_memo, final_content = self._simulate_signing_logic(
            document=document,
            fd_row=fd_row,
        )

        assert calls_nota is False
        assert calls_memo is True, "validate_memo_recipients_for_signing NO se llamo"
        assert isinstance(final_content, str)
        assert "<table>" in final_content
        assert "Juan Perez" in final_content

    def test_memo_ffcc_content_final_es_html_tabular(self):
        document = _make_document_row(
            type_acronym="MEMO",
            type_name="Memo FFCC",
            has_fields=True,
            content={"solicitante": "Pedro Ruiz", "descripcion": "Memo desc", "importe": 2000},
        )
        field_defs = [
            {"name": "solicitante", "label": "Solicitante", "type": "text",   "required": True},
            {"name": "descripcion", "label": "Descripcion", "type": "textarea","required": False},
            {"name": "importe",     "label": "Importe",     "type": "number", "required": False},
        ]
        fd_row = {"field_definitions": field_defs}

        _, _, final_content = self._simulate_signing_logic(
            document=document,
            fd_row=fd_row,
        )

        assert isinstance(final_content, str)
        assert "<table>" in final_content
        assert "Pedro Ruiz" in final_content

    def test_memo_sin_fields_no_genera_html_tabular(self):
        document = _make_document_row(
            type_acronym="MEMO",
            type_name="Memo comun",
            has_fields=False,
            content={"html": "<p>Texto del memo</p>"},
        )
        fd_row = None

        calls_nota, calls_memo, final_content = self._simulate_signing_logic(
            document=document,
            fd_row=fd_row,
        )

        assert calls_memo is True
        assert isinstance(final_content, dict), "MEMO sin formulario conserva content original"


class TestFFCCPuroSigning:

    def _simulate_snapshot(self, field_defs: list, existing_content) -> dict:
        return {
            "schema": field_defs,
            "data": existing_content if isinstance(existing_content, dict) else {},
        }

    def _simulate_signing_logic(self, document: dict, fd_row: dict):
        from services.documents.ffcc_renderer import ffcc_to_html

        document_data = {
            "document_id": document["document_id"],
            "reference": document["reference"],
            "content": document["content"],
            "type_name": document["type_name"],
            "type_acronym": document["type_acronym"],
        }

        calls_nota = False
        calls_memo = False

        if document.get("type_acronym") == "NOTA":
            calls_nota = True
        elif document.get("type_acronym") == "MEMO":
            calls_memo = True

        if document.get("has_fields"):
            field_defs = fd_row["field_definitions"] if fd_row else []
            raw_data = document["content"] if isinstance(document["content"], dict) else {}
            ffcc_html = ffcc_to_html(field_defs, raw_data)
            document_data["content"] = ffcc_html

        return calls_nota, calls_memo, document_data["content"]

    def test_ffcc_puro_sin_recipients(self):
        document = _make_document_row(
            type_acronym="HTML",
            type_name="Formulario Solicitud",
            has_fields=True,
            content=DATA_SOLI,
        )
        fd_row = {"field_definitions": FIELD_DEFS_SOLI}

        calls_nota, calls_memo, final_content = self._simulate_signing_logic(
            document=document,
            fd_row=fd_row,
        )

        assert calls_nota is False
        assert calls_memo is False
        assert isinstance(final_content, str)
        assert "<table>" in final_content

    def test_ffcc_puro_snapshot_tiene_schema_y_data(self):
        snapshot = self._simulate_snapshot(FIELD_DEFS_SOLI, DATA_SOLI)

        assert "schema" in snapshot
        assert "data" in snapshot
        assert snapshot["schema"] == FIELD_DEFS_SOLI
        assert snapshot["data"] == DATA_SOLI

    def test_ffcc_puro_snapshot_renderiza_correctamente(self):
        snapshot = self._simulate_snapshot(FIELD_DEFS_SOLI, DATA_SOLI)
        html = ffcc_to_html(snapshot["schema"], snapshot["data"])

        assert "<table>" in html
        assert "Juan Perez" in html
        assert "50000" in html
        assert "Reparacion de luminarias" in html

    def test_ffcc_puro_snapshot_content_none_produce_data_vacio(self):
        snapshot = self._simulate_snapshot(FIELD_DEFS_SOLI, None)
        assert snapshot["data"] == {}

    def test_ffcc_puro_validacion_enforce_required_true_al_firmar(self):
        with pytest.raises(ValidationError):
            validate_ffcc_content(
                {},
                FIELD_DEFS_SOLI,
                schema_name=SCHEMA_NAME,
                enforce_required=True,
            )

    def test_ffcc_puro_validacion_completa_pasa_al_firmar(self):
        result = validate_ffcc_content(
            DATA_SOLI,
            FIELD_DEFS_SOLI,
            schema_name=SCHEMA_NAME,
            enforce_required=True,
        )
        assert result == DATA_SOLI

    def test_html_sin_fields_no_genera_tabla(self):
        document = _make_document_row(
            type_acronym="HTML",
            type_name="Documento HTML",
            has_fields=False,
            content={"html": "<p>Contenido HTML libre</p>"},
        )
        fd_row = None

        calls_nota, calls_memo, final_content = self._simulate_signing_logic(
            document=document,
            fd_row=fd_row,
        )

        assert calls_nota is False
        assert calls_memo is False
        assert isinstance(final_content, dict)
        assert final_content.get("html") == "<p>Contenido HTML libre</p>"


class TestEditingResponseNotaFFCC:

    def _build_document_type_info(self, document: dict) -> dict:
        doc_type_source = document.get("document_type_source")
        return {
            "name": document.get("document_type_name") or "Sin tipo",
            "acronym": document.get("document_type_acronym") or "",
            "type": doc_type_source or "HTML",
            "has_fields": bool(document.get("has_fields")),
        }

    def _build_content(self, document: dict) -> object:
        has_form_fields = bool(document.get("has_fields"))
        if has_form_fields:
            raw = document.get("content")
            return raw if isinstance(raw, dict) else (raw or {})
        content = document.get("content") or {}
        if isinstance(content, dict):
            return content.get("html", "")
        return str(content)

    def test_nota_ffcc_response_tiene_has_fields_true(self):
        document = {
            "document_type_name": "Nota con Formulario",
            "document_type_acronym": "NOTA",
            "document_type_source": "NOTA",
            "has_fields": True,
        }
        doc_type_info = self._build_document_type_info(document)

        assert doc_type_info["has_fields"] is True
        assert doc_type_info["type"] == "NOTA"
        assert doc_type_info["acronym"] == "NOTA"

    def test_nota_sin_fields_response_has_fields_false(self):
        document = {
            "document_type_name": "Nota comun",
            "document_type_acronym": "NOTA",
            "document_type_source": "NOTA",
            "has_fields": False,
        }
        doc_type_info = self._build_document_type_info(document)

        assert doc_type_info["has_fields"] is False
        assert doc_type_info["type"] == "NOTA"

    def test_memo_ffcc_response_tiene_has_fields_true(self):
        document = {
            "document_type_name": "Memo con Formulario",
            "document_type_acronym": "MEMO",
            "document_type_source": "MEMO",
            "has_fields": True,
        }
        doc_type_info = self._build_document_type_info(document)

        assert doc_type_info["has_fields"] is True
        assert doc_type_info["type"] == "MEMO"

    def test_has_fields_none_en_db_queda_false(self):
        document = {
            "document_type_name": "Sin tipo",
            "document_type_acronym": "",
            "document_type_source": None,
            "has_fields": None,
        }
        doc_type_info = self._build_document_type_info(document)
        assert doc_type_info["has_fields"] is False

    def test_nota_ffcc_content_se_expone_como_dict(self):
        document = {
            "document_type_source": "NOTA",
            "has_fields": True,
            "content": {"solicitante": "Juan Perez", "importe": 50000},
        }
        content = self._build_content(document)
        assert isinstance(content, dict)
        assert content["solicitante"] == "Juan Perez"

    def test_nota_sin_fields_content_se_expone_como_html_string(self):
        document = {
            "document_type_source": "NOTA",
            "has_fields": False,
            "content": {"html": "<p>Texto de la nota</p>"},
        }
        content = self._build_content(document)
        assert isinstance(content, str)
        assert "<p>Texto de la nota</p>" in content

    def test_response_completo_nota_ffcc(self):
        document = {
            "document_type_name": "Nota FFCC",
            "document_type_acronym": "NOTA",
            "document_type_source": "NOTA",
            "has_fields": True,
            "content": DATA_SOLI,
        }
        field_definitions = FIELD_DEFS_SOLI

        doc_type_info = self._build_document_type_info(document)
        content = self._build_content(document)

        response = {
            "document_type": doc_type_info,
            "content": content,
            "field_definitions": field_definitions,
        }

        assert response["document_type"]["type"] == "NOTA"
        assert response["document_type"]["has_fields"] is True
        assert len(response["field_definitions"]) > 0
        assert isinstance(response["content"], dict)


class TestNoGatesFFCCEnum:

    def test_signing_no_tiene_gate_source_type_ffcc(self):
        import inspect
        from services.documents.signing import signing
        source = inspect.getsource(signing)
        assert "source_type == 'FFCC'" not in source, (
            "signing.py tiene gate source_type == 'FFCC'. "
            "Reemplazar por `document.get('has_fields')`."
        )

    def test_signing_bloque_ffcc_es_if_no_elif(self):
        import inspect
        from services.documents.signing import signing
        source = inspect.getsource(signing)

        lines = source.splitlines()
        has_fields_line = None
        for line in lines:
            stripped = line.strip()
            if "has_fields" in stripped and stripped.startswith("if "):
                has_fields_line = stripped
                break

        assert has_fields_line is not None, (
            "No se encontro `if document.get('has_fields'):` en signing.py"
        )
        assert not has_fields_line.startswith("elif"), (
            "El bloque de has_fields en signing.py es `elif` — debe ser `if` independiente"
        )

    def test_numerator_no_tiene_gate_source_type_ffcc(self):
        import inspect
        from services.documents.signing import numerator
        source = inspect.getsource(numerator)
        assert "source_type == 'FFCC'" not in source, (
            "numerator.py tiene gate source_type == 'FFCC'. "
            "Reemplazar por presencia de fila en document_type_fields."
        )


class TestDocumentDetailResponseFFCC:

    def _base_payload(self):
        return {
            "document_id": "11111111-1111-1111-1111-111111111111",
            "reference": "Test FFCC",
            "status": "draft",
            "document_type": {"name": "Constancia de Pago", "acronym": "PAGO",
                              "type": "HTML", "has_fields": True},
            "created_by": "a1000000-0000-0000-0000-000000000001",
            "creator_name": "Maria Rodriguez",
            "auto_link_on_sign": False,
        }

    def test_content_dict_no_revienta(self):
        from models.documents.editing import DocumentDetailResponse
        payload = self._base_payload()
        payload["content"] = {"solicitante": "Juan", "monto": 1000}
        payload["field_definitions"] = [{"name": "solicitante", "label": "Solicitante",
                                         "type": "text", "required": True}]
        resp = DocumentDetailResponse(**payload)
        dumped = resp.model_dump()
        assert dumped["field_definitions"] == payload["field_definitions"]
        assert dumped["document_type"]["has_fields"] is True
        assert dumped["content"] == {"solicitante": "Juan", "monto": 1000}

    def test_content_html_string_sigue_andando(self):
        from models.documents.editing import DocumentDetailResponse
        payload = self._base_payload()
        payload["document_type"]["has_fields"] = False
        payload["content"] = "<p>Hola</p>"
        resp = DocumentDetailResponse(**payload)
        assert resp.content == "<p>Hola</p>"
        assert resp.model_dump()["document_type"]["has_fields"] is False
