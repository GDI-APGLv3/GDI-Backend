import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from shared.exceptions import (
    TransientLookupError,
    ValidationError,
    DocumentNotFoundError,
)


SCHEMA = "100_test"
DOC_ID = "aaaaaaaa-0000-0000-0000-000000000005"
USER_ID = "11111111-1111-1111-1111-000000000005"
CASE_ID = "bbbbbbbb-0000-0000-0000-000000000005"


class TestReservadoGatePrimeraPuerta:

    @pytest.mark.asyncio
    async def test_fetch_vacio_y_doc_existe_es_503(self):
        from services.documents.lifecycle import editing
        from services.documents.signing import lookup_guard

        with patch.object(editing, "fetch_one", AsyncMock(return_value=None)):
            with patch.object(
                lookup_guard, "fetch_val", AsyncMock(return_value=1),
            ):
                with pytest.raises(TransientLookupError):
                    await editing._validate_regla1_proposed_cases(
                        DOC_ID, [CASE_ID], schema_name=SCHEMA,
                    )

    @pytest.mark.asyncio
    async def test_fetch_vacio_y_doc_no_existe_es_404(self):
        from services.documents.lifecycle import editing
        from services.documents.signing import lookup_guard

        with patch.object(editing, "fetch_one", AsyncMock(return_value=None)):
            with patch.object(
                lookup_guard, "fetch_val", AsyncMock(return_value=None),
            ):
                with pytest.raises(DocumentNotFoundError):
                    await editing._validate_regla1_proposed_cases(
                        DOC_ID, [CASE_ID], schema_name=SCHEMA,
                    )

    @pytest.mark.asyncio
    async def test_doc_no_reservado_pasa_sin_fricion(self):
        from services.documents.lifecycle import editing

        fetch_calls = []

        async def fake_fetch_one(*args, **kwargs):
            fetch_calls.append("one")
            return {"doc_reserved": False}

        async def fake_fetch_all(*args, **kwargs):
            fetch_calls.append("all")
            return []

        with patch.object(editing, "fetch_one", fake_fetch_one), \
             patch.object(editing, "fetch_all", fake_fetch_all):
            await editing._validate_regla1_proposed_cases(
                DOC_ID, [CASE_ID], schema_name=SCHEMA,
            )

        assert fetch_calls == ["one"], "no debe llegar a fetch_all si no es reservado"

    @pytest.mark.asyncio
    async def test_doc_reservado_a_case_publico_sigue_bloqueado(self):
        from services.documents.lifecycle import editing

        async def fake_fetch_one(*args, **kwargs):
            return {"doc_reserved": True}

        async def fake_fetch_all(*args, **kwargs):
            return [{"id": CASE_ID}]

        with patch.object(editing, "fetch_one", fake_fetch_one), \
             patch.object(editing, "fetch_all", fake_fetch_all):
            with pytest.raises(ValidationError):
                await editing._validate_regla1_proposed_cases(
                    DOC_ID, [CASE_ID], schema_name=SCHEMA,
                )


class TestCityAcronymFailClosed:

    @pytest.mark.asyncio
    async def test_sin_fila_en_municipalities_falla(self):
        from shared import numbering

        conn = MagicMock()
        conn.fetchrow = AsyncMock(return_value=None)

        with pytest.raises(ValidationError) as ei:
            await numbering._get_city_acronym(conn, "100_desconocido")
        assert "100_desconocido" in str(ei.value.message)

    @pytest.mark.asyncio
    async def test_camino_feliz_devuelve_acronimo(self):
        from shared import numbering

        conn = MagicMock()
        conn.fetchrow = AsyncMock(return_value={"city_acronym": "SMG"})

        acr = await numbering._get_city_acronym(conn, "100_test")
        assert acr == "SMG"


class TestUserDepartmentFallbackFailClosed:

    @pytest.mark.asyncio
    async def test_usuario_sin_sector_y_sin_departamentos_activos_falla(self):
        from shared import numbering

        conn = MagicMock()
        conn.fetchrow = AsyncMock(side_effect=[
            {"dept_acronym": None, "department_id": None},
            None,
        ])

        with pytest.raises(ValidationError) as ei:
            await numbering._get_user_department(conn, USER_ID)
        assert USER_ID in str(ei.value.message)

    @pytest.mark.asyncio
    async def test_usuario_sin_sector_y_dos_departamentos_activos_es_determinista(self):
        from shared import numbering

        dept_id = "cccccccc-0000-0000-0000-000000000001"
        conn = MagicMock()
        conn.fetchrow = AsyncMock(side_effect=[
            {"dept_acronym": None, "department_id": None},
            {"dept_acronym": "ADGEN", "department_id": dept_id},
        ])

        acronym, department_id = await numbering._get_user_department(conn, USER_ID)
        assert acronym == "ADGEN"
        assert department_id == dept_id
        fallback_call = conn.fetchrow.await_args_list[1]
        assert "ORDER BY" in str(fallback_call.args[0]).upper()


class TestTemplateIsReservedFailClosed:

    def _mk_conn(self, template_row):
        conn = MagicMock()
        dept_ok = [{"dept_acronym": "INTE", "municipality_acronym": "SMG"}]
        conn.fetch = AsyncMock(side_effect=[dept_ok, template_row])
        conn.execute = AsyncMock()
        conn.fetchrow = AsyncMock(return_value={"next_sequence": 1})
        return conn

    @pytest.mark.asyncio
    async def test_template_result_vacio_no_crea_expediente(self):
        from services.cases import core

        conn = self._mk_conn(template_row=[])

        with pytest.raises(Exception, match="Template de expediente"):
            await core.create_case(
                conn,
                case_template_id="ct-uuid",
                reference="ref",
                filing_department_id="d1",
                created_by_user_id="u1",
                creator_sector_id="s1",
                owner_sector_id="s1",
                schema_name=SCHEMA,
            )
        insert_calls = [
            c for c in conn.execute.await_args_list
            if c.args and "INSERT INTO cases" in str(c.args[0])
        ]
        assert insert_calls == [], "no debe crearse el expediente si no se pudo verificar is_reserved"

    @pytest.mark.asyncio
    async def test_is_reserved_null_no_crea_expediente(self):
        from services.cases import core

        conn = self._mk_conn(template_row=[{"is_reserved": None}])

        with pytest.raises(Exception, match="Template de expediente"):
            await core.create_case(
                conn,
                case_template_id="ct-uuid",
                reference="ref",
                filing_department_id="d1",
                created_by_user_id="u1",
                creator_sector_id="s1",
                owner_sector_id="s1",
                schema_name=SCHEMA,
            )
        insert_calls = [
            c for c in conn.execute.await_args_list
            if c.args and "INSERT INTO cases" in str(c.args[0])
        ]
        assert insert_calls == []


class TestDetailsBuilderIsReservedRequerido:

    @pytest.mark.asyncio
    async def test_fila_sin_is_reserved_falla_no_filtra_case_number(self):
        from services.documents.signing import details_builder

        rows = [{
            "case_id": CASE_ID,
            "case_number": "EX-2025-00000001-INTE-SMG",
            "reference": None,
            "order_number": 1,
            "linking_date": None,
        }]

        with patch.object(
            details_builder, "fetch_all", AsyncMock(return_value=rows),
        ):
            with pytest.raises(ValueError):
                await details_builder._fetch_linked_cases(
                    DOC_ID, user_id=USER_ID, schema_name=SCHEMA,
                )

    @pytest.mark.asyncio
    async def test_fila_con_is_reserved_none_falla(self):
        from services.documents.signing import details_builder

        rows = [{
            "case_id": CASE_ID,
            "case_number": "EX-2025-00000001-INTE-SMG",
            "reference": None,
            "is_reserved": None,
            "order_number": 1,
            "linking_date": None,
        }]

        with patch.object(
            details_builder, "fetch_all", AsyncMock(return_value=rows),
        ):
            with pytest.raises(ValueError):
                await details_builder._fetch_linked_cases(
                    DOC_ID, user_id=USER_ID, schema_name=SCHEMA,
                )

    @pytest.mark.asyncio
    async def test_reservado_sin_permiso_no_devuelve_case_number(self):
        from services.documents.signing import details_builder

        rows = [{
            "case_id": CASE_ID,
            "case_number": "SECRETO-2025-00000009-INTE-SMG",
            "reference": None,
            "is_reserved": True,
            "order_number": 1,
            "linking_date": None,
        }]

        with patch.object(
            details_builder, "fetch_all", AsyncMock(return_value=rows),
        ), patch(
            "services.cases.permissions.can_user_view_case",
            AsyncMock(return_value=False),
        ):
            out = await details_builder._fetch_linked_cases(
                DOC_ID, user_id=USER_ID, schema_name=SCHEMA,
            )
        assert out == [], "el case_number del reservado NO debe filtrarse"

    @pytest.mark.asyncio
    async def test_publico_pasa_normalmente(self):
        from services.documents.signing import details_builder

        rows = [{
            "case_id": CASE_ID,
            "case_number": "EX-2025-00000001-INTE-SMG",
            "reference": "Asunto público",
            "is_reserved": False,
            "order_number": 1,
            "linking_date": None,
        }]

        with patch.object(
            details_builder, "fetch_all", AsyncMock(return_value=rows),
        ):
            out = await details_builder._fetch_linked_cases(
                DOC_ID, user_id=USER_ID, schema_name=SCHEMA,
            )
        assert len(out) == 1
        assert out[0]["case_number"] == "EX-2025-00000001-INTE-SMG"
        assert out[0]["is_reserved"] is False
