
import pytest

from services.cases.retrieval import _build_where_conditions, _build_where_params


TERMINO = "habilitacion"


def _where(search=TERMINO):
    sql, _next_idx = _build_where_conditions(
        search_filter=search,
        resolved_sector=None,
        resolved_tratas=[],
        date_filter=None,
        date_from=None,
        date_to=None,
    )
    return sql


class TestPredicadoBusquedaExpedientes:

    def test_usa_immutable_unaccent_y_no_unaccent_pelado(self):
        sql = _where()

        assert "public.immutable_unaccent(" in sql, (
            "el predicado tiene que llamar a `public.immutable_unaccent(...)` o los "
            "indices trigram de las migraciones 114 y 115 NO se usan (son indices de "
            "EXPRESION: el planner los ignora en silencio si el predicado no coincide "
            "literal). Ver GDI-365."
        )

        sin_wrapper = sql.replace("public.immutable_unaccent(", "")
        assert "unaccent(" not in sin_wrapper, (
            "quedo un `unaccent(...)` sin envolver. Devuelve los mismos resultados "
            "pero apaga el indice trigram sin que nada falle. Ver GDI-365."
        )

    def test_no_escanea_el_html_de_los_documentos_vinculados(self):
        sql = _where()

        assert "content" not in sql, (
            "el predicado no debe tocar `od.content->>'html'`: es el HTML COMPLETO de "
            "cada documento firmado del expediente. Es lo mas caro de la busqueda y "
            "ademas trae expedientes que apenas mencionan la palabra en el cuerpo. La "
            "mig 114 ya habia decidido no indexar content_html por el mismo motivo."
        )

    def test_no_usa_similarity(self):
        sql = _where()

        assert "similarity(" not in sql, (
            "`similarity(...) > 0.3` no lo convierte el planner a operador indexable: "
            "corre fila por fila sobre cada referencia. Y con referencias largas el "
            "umbral casi nunca dispara, asi que pagaba costo sin dar resultados. Si "
            "alguna vez se quiere tolerancia a errores de tipeo de verdad, la forma "
            "correcta es el operador `%` con un GIN sobre la MISMA expresion."
        )

    def test_busca_en_los_tres_campos_que_se_pidieron(self):
        sql = _where()

        assert "c.reference" in sql, "falta buscar por referencia del expediente"
        assert "c.case_number" in sql, "falta buscar por numero de expediente"
        assert "od.reference" in sql, (
            "falta buscar por la referencia de los documentos vinculados"
        )
        assert "od.official_number" in sql, (
            "falta buscar por el numero de los documentos vinculados"
        )

    def test_la_referencia_del_documento_va_sin_coalesce(self):
        sql = _where()

        assert "COALESCE(od.reference" not in sql, (
            "`official_documents.reference` es NOT NULL, y el indice "
            "idx_<schema>_official_reference_trgm se creo sobre "
            "`immutable_unaccent(LOWER(reference))` SIN coalesce. Envolverlo rompe la "
            "coincidencia literal y apaga el indice."
        )

    def test_los_documentos_vinculados_siguen_acotados(self):
        sql = _where()

        assert "cod.is_active = true" in sql, "el vinculo tiene que estar activo"
        assert "od.signed_at IS NOT NULL" in sql, "el documento tiene que estar firmado"

    def test_sin_busqueda_no_hay_predicado_de_texto(self):
        sql = _where(search=None)

        assert "immutable_unaccent" not in sql
        assert "case_official_documents" not in sql


class TestParamsDelPredicado:

    def test_cantidad_de_params_coincide_con_los_placeholders(self):
        sql, next_idx = _build_where_conditions(
            search_filter=TERMINO,
            resolved_sector=None,
            resolved_tratas=[],
            date_filter=None,
            date_from=None,
            date_to=None,
        )
        params = _build_where_params(
            search_filter=TERMINO,
            resolved_sector=None,
            resolved_tratas=[],
            date_from=None,
            date_to=None,
        )

        consumidos = next_idx - 3
        assert consumidos == len(params), (
            f"el SQL consume {consumidos} params y _build_where_params produce "
            f"{len(params)}. Divergen -> asyncpg explota en runtime."
        )

    def test_la_busqueda_consume_exactamente_cuatro_params(self):
        _sql, next_idx = _build_where_conditions(
            search_filter=TERMINO,
            resolved_sector=None,
            resolved_tratas=[],
            date_filter=None,
            date_from=None,
            date_to=None,
        )
        assert next_idx - 3 == 4, (
            "la busqueda de expedientes usa 4 params: referencia y numero del "
            "expediente, mas numero y referencia del documento vinculado"
        )

    def test_todos_los_params_son_patrones_like(self):
        params = _build_where_params(
            search_filter=TERMINO,
            resolved_sector=None,
            resolved_tratas=[],
            date_from=None,
            date_to=None,
        )
        assert params == [f"%{TERMINO}%"] * 4
