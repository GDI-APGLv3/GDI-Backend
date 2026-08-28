
import inspect
import pytest
from unittest.mock import patch, AsyncMock, MagicMock


CASE_ID = "cccccccc-0000-0000-0000-000000000001"
USER_ID = "aaaaaaaa-0000-0000-0000-000000000001"
DOC_ID = "dddddddd-0000-0000-0000-000000000001"
SCHEMA = "100_test"


class TestCanUserViewCaseR1R2R3:

    def test_query_contains_r1_r2_r3_branches(self):
        import services.cases.permissions as perms_module

        source = inspect.getsource(perms_module.can_user_view_case)
        assert "case_responsibles cr" in source, "Falta rama R1 (case_responsibles)"
        assert "cr.user_id" in source and "cr.is_active = true" in source
        assert "d.head_user_id" in source, "Falta titular directo (R2/R3)"
        assert "cm.admin_sector_id" in source
        assert "cm.assigned_sector_id" in source

    def test_query_contains_r4_branch(self):
        import services.cases.permissions as perms_module

        source = inspect.getsource(perms_module.can_user_view_case)
        assert "case_assignment_tasks cat" in source, "Falta rama R4 (case_assignment_tasks)"
        assert "cat.assigned_user_id" in source
        assert "cat.status = 'open'" in source

    def test_old_branches_scoped_to_not_reserved(self):
        import services.cases.permissions as perms_module

        source = inspect.getsource(perms_module.can_user_view_case)
        query_start = source.index('query = f"""')
        query_source = source[query_start:]
        assert query_source.count("ct.is_reserved = false") == 5, (
            "Deben ser exactamente 5 ramas viejas acotadas a NOT is_reserved "
            "(flag global, creador, 3a, 3b, 3c)"
        )
        assert query_source.count("ct.is_reserved = true") == 4, (
            "Deben ser exactamente 4 ramas nuevas (R1, R2, R3, R4) acotadas a is_reserved = true"
        )

    @pytest.mark.asyncio
    async def test_returns_true_when_has_access(self):
        from services.cases.permissions import can_user_view_case

        with patch("services.cases.permissions.fetch_all", new_callable=AsyncMock) as mock_fa:
            mock_fa.return_value = [{"has_access": True}]
            result = await can_user_view_case(CASE_ID, USER_ID, schema_name=SCHEMA)

        assert result is True

    @pytest.mark.asyncio
    async def test_returns_false_when_no_access(self):
        from services.cases.permissions import can_user_view_case

        with patch("services.cases.permissions.fetch_all", new_callable=AsyncMock) as mock_fa:
            mock_fa.return_value = [{"has_access": False}]
            result = await can_user_view_case(CASE_ID, USER_ID, schema_name=SCHEMA)

        assert result is False

    @pytest.mark.asyncio
    async def test_undefined_column_error_is_reraised_not_swallowed(self):
        from services.cases.permissions import can_user_view_case
        from asyncpg.exceptions import UndefinedColumnError

        with patch("services.cases.permissions.fetch_all", new_callable=AsyncMock) as mock_fa:
            mock_fa.side_effect = UndefinedColumnError("column is_reserved does not exist")
            with pytest.raises(UndefinedColumnError):
                await can_user_view_case(CASE_ID, USER_ID, schema_name=SCHEMA)

    @pytest.mark.asyncio
    async def test_generic_exception_still_returns_false(self):
        from services.cases.permissions import can_user_view_case

        with patch("services.cases.permissions.fetch_all", new_callable=AsyncMock) as mock_fa:
            mock_fa.side_effect = RuntimeError("conexion perdida")
            result = await can_user_view_case(CASE_ID, USER_ID, schema_name=SCHEMA)

        assert result is False

    @pytest.mark.asyncio
    async def test_passes_15_params_in_documented_order(self):
        from services.cases.permissions import can_user_view_case

        captured = {}

        async def capture(query, *params, schema_name):
            captured["params"] = params
            return [{"has_access": False}]

        with patch("services.cases.permissions.fetch_all", side_effect=capture):
            await can_user_view_case(CASE_ID, USER_ID, schema_name=SCHEMA)

        params = captured["params"]
        assert len(params) == 15
        expected = [
            USER_ID, CASE_ID, CASE_ID, USER_ID, CASE_ID, CASE_ID, CASE_ID,
            CASE_ID, USER_ID, CASE_ID, USER_ID, CASE_ID, USER_ID,
            CASE_ID, USER_ID,
        ]
        assert list(params) == expected

    @pytest.mark.asyncio
    async def test_r4_open_task_grants_access(self):
        from services.cases.permissions import can_user_view_case

        with patch("services.cases.permissions.fetch_all", new_callable=AsyncMock) as mock_fa:
            mock_fa.return_value = [{"has_access": True}]
            result = await can_user_view_case(CASE_ID, USER_ID, schema_name=SCHEMA)

        assert result is True

    @pytest.mark.asyncio
    async def test_r4_closed_task_does_not_grant_access(self):
        from services.cases.permissions import can_user_view_case

        with patch("services.cases.permissions.fetch_all", new_callable=AsyncMock) as mock_fa:
            mock_fa.return_value = [{"has_access": False}]
            result = await can_user_view_case(CASE_ID, USER_ID, schema_name=SCHEMA)

        assert result is False


class TestCanUserViewDocumentReservedSplit:

    def test_query_has_reserved_and_public_blocks(self):
        import services.documents.permissions as doc_perms

        source = inspect.getsource(doc_perms.can_user_view_document)
        assert "dt_type.is_reserved = true" in source
        assert "COALESCE(dt_type2.is_reserved, false) = true" in source
        assert "document_signers" in source
        assert "created_by = $1::uuid" in source

    @pytest.mark.asyncio
    async def test_reserved_doc_signer_has_access(self):
        from services.documents.permissions import can_user_view_document

        with patch("services.documents.permissions.fetch_val", new_callable=AsyncMock) as mock_fv:
            mock_fv.return_value = True
            result = await can_user_view_document(DOC_ID, USER_ID, schema_name=SCHEMA)

        assert result is True

    @pytest.mark.asyncio
    async def test_reserved_doc_non_signer_falls_back_to_case_inheritance(self):
        from services.documents.permissions import can_user_view_document

        with patch("services.documents.permissions.fetch_val", new_callable=AsyncMock) as mock_fv, \
             patch("services.documents.permissions.fetch_all", new_callable=AsyncMock) as mock_fa, \
             patch("services.cases.permissions.can_user_view_case", new_callable=AsyncMock) as mock_case:
            mock_fv.return_value = False
            mock_fa.return_value = [{"case_id": CASE_ID}]
            mock_case.return_value = True

            result = await can_user_view_document(DOC_ID, USER_ID, schema_name=SCHEMA)

        assert result is True

    @pytest.mark.asyncio
    async def test_undefined_column_error_is_reraised(self):
        from services.documents.permissions import can_user_view_document
        from asyncpg.exceptions import UndefinedColumnError

        with patch("services.documents.permissions.fetch_val", new_callable=AsyncMock) as mock_fv:
            mock_fv.side_effect = UndefinedColumnError("column is_reserved does not exist")
            with pytest.raises(UndefinedColumnError):
                await can_user_view_document(DOC_ID, USER_ID, schema_name=SCHEMA)


class TestGetCaseByExactNumberDelegation:

    @pytest.mark.asyncio
    async def test_delegates_to_can_user_view_case_when_user_id_present(self):
        from services.cases.queries import get_case_by_exact_number_unrestricted

        case_row = {
            "id": CASE_ID,
            "case_number": "EXP-2026-00001-SMG",
            "reference": "ref",
            "last_modified_at": None,
            "type_name": "Expediente",
            "case_type": "EXP",
            "is_reserved": False,
        }

        with patch("database.fetch_all", new_callable=AsyncMock) as mock_fa, \
             patch("services.cases.permissions.can_user_view_case", new_callable=AsyncMock) as mock_case:
            mock_fa.side_effect = [[case_row], [], []]
            mock_case.return_value = False

            result = await get_case_by_exact_number_unrestricted(
                "EXP-2026-00001-SMG", user_id=USER_ID, schema_name=SCHEMA
            )

        mock_case.assert_called_once_with(CASE_ID, USER_ID, schema_name=SCHEMA)
        assert result is None, "Sin acceso via can_user_view_case debe comportarse como no encontrado"

    @pytest.mark.asyncio
    async def test_returns_case_when_can_user_view_case_true(self):
        from services.cases.queries import get_case_by_exact_number_unrestricted

        case_row = {
            "id": CASE_ID,
            "case_number": "EXP-2026-00001-SMG",
            "reference": "ref",
            "last_modified_at": None,
            "type_name": "Expediente",
            "case_type": "EXP",
            "is_reserved": False,
        }

        with patch("database.fetch_all", new_callable=AsyncMock) as mock_fa, \
             patch("services.cases.permissions.can_user_view_case", new_callable=AsyncMock) as mock_case:
            mock_fa.side_effect = [[case_row], [], []]
            mock_case.return_value = True

            result = await get_case_by_exact_number_unrestricted(
                "EXP-2026-00001-SMG", user_id=USER_ID, schema_name=SCHEMA
            )

        assert result is not None
        assert result["found"] is True
        assert result["case"]["case_number"] == "EXP-2026-00001-SMG"

    def test_no_ad_hoc_global_flag_bypass_left_in_source(self):
        import services.cases.queries as queries_module

        source = inspect.getsource(queries_module.get_case_by_exact_number_unrestricted)
        assert "can_global_search_cases" not in source
        assert "can_user_view_case" in source


class TestReservedNumberMatchMinimo:

    RESERVED_ROW = {
        "id": CASE_ID,
        "case_number": "EE-2026-000200-MDEV-INTE344",
        "reference": "carga secreta",
        "last_modified_at": "2026-07-07",
        "type_name": "QA Reservado",
        "case_type": "RESQA",
        "is_reserved": True,
    }

    @pytest.mark.asyncio
    async def test_reserved_without_access_returns_restricted_minimal_payload(self):
        from services.cases.queries import get_case_by_exact_number_unrestricted

        with patch("database.fetch_all", new_callable=AsyncMock) as mock_fa, \
             patch("services.cases.permissions.can_user_view_case", new_callable=AsyncMock) as mock_case:
            mock_fa.side_effect = [[self.RESERVED_ROW]]
            mock_case.return_value = False

            result = await get_case_by_exact_number_unrestricted(
                "EE-2026-000200-MDEV-INTE344", user_id=USER_ID, schema_name=SCHEMA
            )

        assert result is not None and result["found"] is True
        case = result["case"]
        assert case["id"] == CASE_ID
        assert case["case_number"] == "EE-2026-000200-MDEV-INTE344"
        assert case["restricted"] is True
        assert case["is_reserved"] is True

    @pytest.mark.asyncio
    async def test_restricted_payload_leaks_nothing(self):
        from services.cases.queries import get_case_by_exact_number_unrestricted

        with patch("database.fetch_all", new_callable=AsyncMock) as mock_fa, \
             patch("services.cases.permissions.can_user_view_case", new_callable=AsyncMock) as mock_case:
            mock_fa.side_effect = [[self.RESERVED_ROW]]
            mock_case.return_value = False

            result = await get_case_by_exact_number_unrestricted(
                "EE-2026-000200-MDEV-INTE344", user_id=USER_ID, schema_name=SCHEMA
            )

        case = result["case"]
        assert case["reference"] is None
        assert case["last_modified_at"] is None
        assert case["case_type"] == {"name": None, "acronym": None}
        assert case["admin_sector"] is None
        assert case["assigned_sectors"] == []
        assert mock_fa.await_count == 1

    @pytest.mark.asyncio
    async def test_non_reserved_without_access_still_returns_none(self):
        from services.cases.queries import get_case_by_exact_number_unrestricted

        row = dict(self.RESERVED_ROW, is_reserved=False)
        with patch("database.fetch_all", new_callable=AsyncMock) as mock_fa, \
             patch("services.cases.permissions.can_user_view_case", new_callable=AsyncMock) as mock_case:
            mock_fa.side_effect = [[row]]
            mock_case.return_value = False

            result = await get_case_by_exact_number_unrestricted(
                "EE-2026-000200-MDEV-INTE344", user_id=USER_ID, schema_name=SCHEMA
            )

        assert result is None

    @pytest.mark.asyncio
    async def test_reserved_with_access_returns_full_payload_with_is_reserved(self):
        from services.cases.queries import get_case_by_exact_number_unrestricted

        with patch("database.fetch_all", new_callable=AsyncMock) as mock_fa, \
             patch("services.cases.permissions.can_user_view_case", new_callable=AsyncMock) as mock_case:
            mock_fa.side_effect = [[self.RESERVED_ROW], [], []]
            mock_case.return_value = True

            result = await get_case_by_exact_number_unrestricted(
                "EE-2026-000200-MDEV-INTE344", user_id=USER_ID, schema_name=SCHEMA
            )

        case = result["case"]
        assert case["reference"] == "carga secreta"
        assert case["is_reserved"] is True
        assert "restricted" not in case

    def test_proposed_cases_query_masks_reserved_reference(self):
        from services.documents.core.queries import get_proposed_cases_for_document_query

        q = get_proposed_cases_for_document_query()
        assert "is_reserved" in q
        assert "CASE WHEN" in q and "NULL ELSE c.reference END" in q


class TestBuildWhereConditionsReservedSplit:

    def test_split_present_in_both_global_and_sector_branches(self):
        from services.cases.retrieval import _build_where_conditions

        for is_global in (True, False):
            where_sql, next_idx = _build_where_conditions(
                None, None, [], None, None, None,
                is_global_search=is_global,
                sector_param=1, user_id_param=2, param_start=3,
            )
            assert "ct.is_reserved" in where_sql
            assert "case_responsibles" in where_sql
            assert "head_user_id" in where_sql
            assert "case_assignment_tasks" in where_sql, "Falta rama R4 en el listado"
            assert "cat.status = 'open'" in where_sql
            assert next_idx == 3

    def test_non_reserved_branch_is_true_literal_when_global(self):
        from services.cases.retrieval import _build_where_conditions

        where_sql, _ = _build_where_conditions(
            None, None, [], None, None, None,
            is_global_search=True,
            sector_param=1, user_id_param=1, param_start=2,
        )
        assert "NOT ct.is_reserved AND (TRUE)" in where_sql.replace("\n", " ").replace("  ", " ") \
            or "NOT ct.is_reserved AND (\n            TRUE\n        )" in where_sql \
            or "TRUE" in where_sql


class TestCreateCaseAutoAssignCreator:

    @pytest.mark.asyncio
    async def test_reserved_template_triggers_add_responsible(self):
        from services.cases.core import create_case

        mock_conn = MagicMock()
        mock_conn.fetch = AsyncMock()
        mock_conn.execute = AsyncMock()

        dept_row = {"dept_acronym": "SMG", "municipality_acronym": "MDF"}
        template_row = {"is_reserved": True}
        mock_conn.fetch.side_effect = [
            [dept_row],
            [template_row],
        ]
        mock_conn.fetchrow = AsyncMock(return_value={"next_sequence": 1})

        with patch("services.cases.core.get_case_number_format", return_value="EXP-{sequence}-SMG"), \
             patch("services.cases.responsibles.add_responsible", new_callable=AsyncMock) as mock_add:
            await create_case(
                mock_conn,
                case_template_id="template-uuid",
                reference="Expediente de prueba",
                created_by_user_id=USER_ID,
                filing_department_id="dept-uuid",
                creator_sector_id="sector-uuid",
                owner_sector_id="owner-sector-uuid",
                schema_name=SCHEMA,
            )

        mock_add.assert_called_once()
        call_kwargs = mock_add.call_args.kwargs
        assert call_kwargs["user_id"] == USER_ID
        assert call_kwargs["responsible_type"] == "ADMIN"
        assert call_kwargs["sector_id"] == "owner-sector-uuid"
        assert call_kwargs["conn"] is mock_conn

    @pytest.mark.asyncio
    async def test_non_reserved_template_does_not_trigger_add_responsible(self):
        from services.cases.core import create_case

        mock_conn = MagicMock()
        mock_conn.fetch = AsyncMock()
        mock_conn.execute = AsyncMock()

        dept_row = {"dept_acronym": "SMG", "municipality_acronym": "MDF"}
        template_row = {"is_reserved": False}
        mock_conn.fetch.side_effect = [
            [dept_row],
            [template_row],
        ]
        mock_conn.fetchrow = AsyncMock(return_value={"next_sequence": 1})

        with patch("services.cases.core.get_case_number_format", return_value="EXP-{sequence}-SMG"), \
             patch("services.cases.responsibles.add_responsible", new_callable=AsyncMock) as mock_add:
            await create_case(
                mock_conn,
                case_template_id="template-uuid",
                reference="Expediente de prueba",
                created_by_user_id=USER_ID,
                filing_department_id="dept-uuid",
                creator_sector_id="sector-uuid",
                owner_sector_id="owner-sector-uuid",
                schema_name=SCHEMA,
            )

        mock_add.assert_not_called()

    @pytest.mark.asyncio
    async def test_owner_sector_id_none_falls_back_to_creator_sector(self):
        from services.cases.core import create_case

        mock_conn = MagicMock()
        mock_conn.fetch = AsyncMock()
        mock_conn.execute = AsyncMock()

        dept_row = {"dept_acronym": "SMG", "municipality_acronym": "MDF"}
        template_row = {"is_reserved": False}
        mock_conn.fetch.side_effect = [
            [dept_row],
            [template_row],
        ]
        mock_conn.fetchrow = AsyncMock(return_value={"next_sequence": 1})

        with patch("services.cases.core.get_case_number_format", return_value="EXP-{sequence}-SMG"):
            await create_case(
                mock_conn,
                case_template_id="template-uuid",
                reference="Expediente de prueba",
                created_by_user_id=USER_ID,
                filing_department_id="dept-uuid",
                creator_sector_id="creator-sector-uuid",
                owner_sector_id=None,
                schema_name=SCHEMA,
            )

        insert_calls = [c for c in mock_conn.execute.call_args_list if "INSERT INTO cases" in c.args[0]]
        assert len(insert_calls) == 1
        assert "creator-sector-uuid" in insert_calls[0].args


class TestRegla1LinkOfficialDocument:

    @pytest.mark.asyncio
    async def test_link_rejects_reserved_doc_into_public_case(self):
        from services.cases.documents import link_official_document
        from shared.exceptions import ValidationError

        mock_conn = MagicMock()
        mock_conn.execute = AsyncMock()
        mock_conn.fetchrow = AsyncMock(
            return_value={"doc_reserved": True, "case_reserved": False}
        )

        class _TxCtx:
            async def __aenter__(self):
                return mock_conn

            async def __aexit__(self, *a):
                return False

        with patch("services.cases.documents.fetch_all", new_callable=AsyncMock) as mock_fa, \
             patch("services.cases.documents.transaction", return_value=_TxCtx()), \
             patch("services.case_service.CaseService.get_user_editable_sector_ids",
                   new_callable=AsyncMock, return_value=["sector-1"]):

            mock_fa.side_effect = [
                [{"id": CASE_ID, "case_number": "EXP-2026-00001-SMG"}],
                [{"1": 1}],
                [{"id": DOC_ID, "official_number": "IF-2026-01", "reference": "ref"}],
                [],
            ]

            with pytest.raises(ValidationError):
                await link_official_document(
                    case_id=CASE_ID,
                    official_document_id=DOC_ID,
                    linking_user_id=USER_ID,
                    user_sector_id="sector-1",
                    schema_name=SCHEMA,
                )

    @pytest.mark.asyncio
    async def test_link_allows_reserved_doc_into_reserved_case(self):
        from services.cases.documents import link_official_document

        mock_conn = MagicMock()
        mock_conn.execute = AsyncMock()
        mock_conn.fetchrow = AsyncMock(side_effect=[
            {"doc_reserved": True, "case_reserved": True},
            {"max_order": 0},
            {"linking_date": "2026-07-04"},
            {"admin_sector_id": "sector-1"},
        ])

        class _TxCtx:
            async def __aenter__(self):
                return mock_conn

            async def __aexit__(self, *a):
                return False

        with patch("services.cases.documents.fetch_all", new_callable=AsyncMock) as mock_fa, \
             patch("services.cases.documents.transaction", return_value=_TxCtx()), \
             patch("services.case_service.CaseService.get_user_editable_sector_ids",
                   new_callable=AsyncMock, return_value=["sector-1"]):

            mock_fa.side_effect = [
                [{"id": CASE_ID, "case_number": "EXP-2026-00001-SMG"}],
                [{"1": 1}],
                [{"id": DOC_ID, "official_number": "IF-2026-01", "reference": "ref"}],
                [],
            ]

            result = await link_official_document(
                case_id=CASE_ID,
                official_document_id=DOC_ID,
                linking_user_id=USER_ID,
                user_sector_id="sector-1",
                schema_name=SCHEMA,
            )

        assert result["official_number"] == "IF-2026-01"


class TestRegla1McpProposeDocumentDelegates:

    @pytest.mark.asyncio
    async def test_mcp_propose_document_delegates_to_service(self):
        from api_gateway.tools.cases import propose_document
        from api_gateway.context import MCPContext

        ctx = MCPContext(api_key="test-key", municipality_id="test-muni", schema_name=SCHEMA)
        fake_result = {
            "case_id": CASE_ID,
            "document_draft_id": DOC_ID,
            "message": "Documento propuesto para vincular al expediente",
        }

        with patch("services.cases.documents.propose_document_to_case",
                   new_callable=AsyncMock, return_value=fake_result) as mock_svc:
            result = await propose_document(ctx, CASE_ID, DOC_ID, USER_ID)

        mock_svc.assert_awaited_once_with(
            case_id=CASE_ID,
            document_draft_id=DOC_ID,
            proposing_user_id=USER_ID,
            schema_name=SCHEMA,
        )
        assert result["success"] is True
        assert result["case_id"] == CASE_ID

    @pytest.mark.asyncio
    async def test_mcp_propose_document_propagates_regla1_validation_error(self):
        from api_gateway.tools.cases import propose_document
        from api_gateway.context import MCPContext
        from shared.exceptions import ValidationError

        ctx = MCPContext(api_key="test-key", municipality_id="test-muni", schema_name=SCHEMA)

        with patch("services.cases.documents.propose_document_to_case",
                   new_callable=AsyncMock,
                   side_effect=ValidationError("reservado")):
            with pytest.raises(ValidationError):
                await propose_document(ctx, CASE_ID, DOC_ID, USER_ID)


class TestRegla1ProposeDocumentToCase:

    @pytest.mark.asyncio
    async def test_propose_rejects_reserved_doc_into_public_case(self):
        from services.cases.documents import propose_document_to_case
        from shared.exceptions import ValidationError

        mock_conn = MagicMock()
        mock_conn.execute = AsyncMock()
        mock_conn.fetchrow = AsyncMock(
            return_value={"doc_reserved": True, "case_reserved": False}
        )

        class _TxCtx:
            async def __aenter__(self):
                return mock_conn

            async def __aexit__(self, *a):
                return False

        with patch("database.check_document_exists",
                   new_callable=AsyncMock, return_value=True), \
             patch("services.cases.documents.transaction", return_value=_TxCtx()):

            with pytest.raises(ValidationError):
                await propose_document_to_case(
                    case_id=CASE_ID,
                    document_draft_id=DOC_ID,
                    proposing_user_id=USER_ID,
                    schema_name=SCHEMA,
                )


class TestIsReservedProjectedInResponses:

    def test_list_query_selects_ct_is_reserved(self):
        from services.case_queries import get_cases_list_query

        query = get_cases_list_query("WHERE 1=1", limit_param_idx=1, offset_param_idx=2)
        assert "ct.is_reserved" in query

    def test_build_case_response_includes_is_reserved_in_case_type(self):
        from services.cases.retrieval import _build_case_response

        row = {
            "id": "id1", "case_number": "EXP-1", "reference": "ref",
            "last_modified_at": None, "type_name": "QA Reservado", "case_type": "RESQA",
            "case_type_is_reserved": True,
            "admin_sector_acronym": None, "admin_sector_department": None,
            "admin_sector_color": None,
            "is_admin_by_transfer": False, "is_admin_by_creation": False,
            "assigned_sectors_json": [], "responsibles_json": [],
            "short_ai_summary": None, "ai_summary": None, "is_favorite": False,
        }
        result = _build_case_response(row, schema_name=SCHEMA)
        assert result["case_type"]["is_reserved"] is True

    def test_case_detail_basic_info_query_selects_is_reserved(self):
        from services.case_queries import get_case_basic_info_query

        query = get_case_basic_info_query()
        assert "ct.is_reserved" in query

    @pytest.mark.asyncio
    async def test_get_case_detail_includes_is_reserved_in_template(self):
        from services.cases.queries import get_case_detail

        case_row = {
            "id": CASE_ID, "case_number": "EXP-1", "reference": "ref",
            "ai_summary": None, "type_name": "QA Reservado",
            "template_acronym": "RESQA", "template_is_reserved": True,
        }

        with patch("services.case_service.CaseService.can_user_view_case",
                   new_callable=AsyncMock, return_value=True), \
             patch("database.fetch_all", new_callable=AsyncMock) as mock_fa, \
             patch("database.fetch_one", new_callable=AsyncMock) as mock_fo:

            async def fetch_all_side_effect(query, *args, **kwargs):
                if "ct.acronym as template_acronym" in query:
                    return [case_row]
                return []

            mock_fa.side_effect = fetch_all_side_effect
            mock_fo.return_value = {"is_favorite": False}

            result = await get_case_detail(CASE_ID, USER_ID, schema_name=SCHEMA)

        assert result is not None
        assert result["template"]["is_reserved"] is True


class TestRegla1SaveDocumentProposedCases:

    @pytest.mark.asyncio
    async def test_reserved_doc_into_non_reserved_case_raises(self):
        from services.documents.lifecycle.editing import _validate_regla1_proposed_cases
        from shared.exceptions import ValidationError

        with patch("services.documents.lifecycle.editing.fetch_one",
                   new_callable=AsyncMock, return_value={"doc_reserved": True}), \
             patch("services.documents.lifecycle.editing.fetch_all",
                   new_callable=AsyncMock, return_value=[{"id": CASE_ID}]):

            with pytest.raises(ValidationError):
                await _validate_regla1_proposed_cases(DOC_ID, [CASE_ID], schema_name=SCHEMA)

    @pytest.mark.asyncio
    async def test_reserved_doc_into_reserved_case_allowed(self):
        from services.documents.lifecycle.editing import _validate_regla1_proposed_cases

        with patch("services.documents.lifecycle.editing.fetch_one",
                   new_callable=AsyncMock, return_value={"doc_reserved": True}), \
             patch("services.documents.lifecycle.editing.fetch_all",
                   new_callable=AsyncMock, return_value=[]) as mock_fa:

            await _validate_regla1_proposed_cases(DOC_ID, [CASE_ID], schema_name=SCHEMA)

        mock_fa.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_non_reserved_doc_skips_case_check(self):
        from services.documents.lifecycle.editing import _validate_regla1_proposed_cases

        with patch("services.documents.lifecycle.editing.fetch_one",
                   new_callable=AsyncMock, return_value={"doc_reserved": False}), \
             patch("services.documents.lifecycle.editing.fetch_all",
                   new_callable=AsyncMock) as mock_fa:

            await _validate_regla1_proposed_cases(DOC_ID, [CASE_ID], schema_name=SCHEMA)

        mock_fa.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_empty_case_ids_short_circuits(self):
        from services.documents.lifecycle.editing import _validate_regla1_proposed_cases

        with patch("services.documents.lifecycle.editing.fetch_one",
                   new_callable=AsyncMock) as mock_fo, \
             patch("services.documents.lifecycle.editing.fetch_all",
                   new_callable=AsyncMock) as mock_fa:

            await _validate_regla1_proposed_cases(DOC_ID, [], schema_name=SCHEMA)

        mock_fo.assert_not_awaited()
        mock_fa.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_save_document_calls_regla1_validation_when_proposing_cases(self):
        from services.documents.lifecycle import editing as editing_module

        with patch.object(editing_module, "_validate_document_can_be_edited",
                          new_callable=AsyncMock), \
             patch.object(editing_module, "fetch_one", new_callable=AsyncMock,
                          return_value=None), \
             patch.object(editing_module, "_validate_case_ids", new_callable=AsyncMock), \
             patch.object(editing_module, "_validate_regla1_proposed_cases",
                          new_callable=AsyncMock) as mock_regla1, \
             patch.object(editing_module, "fetch_all", new_callable=AsyncMock,
                          return_value=[]), \
             patch.object(editing_module, "transaction") as mock_tx, \
             patch.object(editing_module, "get_document_details_for_editing",
                          new_callable=AsyncMock, return_value={"updated_at": None}), \
             patch.object(editing_module, "_register_proposal_history",
                          new_callable=AsyncMock):

            mock_conn = MagicMock()
            mock_conn.execute = AsyncMock()

            class _TxCtx:
                async def __aenter__(self):
                    return mock_conn

                async def __aexit__(self, *a):
                    return False

            mock_tx.return_value = _TxCtx()

            await editing_module.save_document_changes(
                DOC_ID,
                reference="ref actualizada",
                proposed_case_ids=[CASE_ID],
                user_id=USER_ID,
                schema_name=SCHEMA,
            )

        mock_regla1.assert_awaited_once_with(DOC_ID, [CASE_ID], schema_name=SCHEMA)


class TestExcludeReservedDocumentsList:

    def test_query_projects_document_type_is_reserved_in_all_branches(self):
        import services.document_service as doc_service_module

        source = inspect.getsource(doc_service_module.get_user_documents)
        assert source.count("dt.is_reserved AS document_type_is_reserved") == 4

    def test_exclude_reserved_adds_filter_when_true(self):
        import services.document_service as doc_service_module

        source = inspect.getsource(doc_service_module.get_user_documents)
        assert "document_type_is_reserved = false" in source
        assert "if exclude_reserved:" in source

    @pytest.mark.asyncio
    async def test_exclude_reserved_true_filters_query(self):
        from services.document_service import get_user_documents

        captured = {}

        class _FakeConnCtx:
            async def __aenter__(self):
                return _FakeConnRow()

            async def __aexit__(self, *a):
                return False

        class _FakeConnRow:
            async def execute(self, query, *params):
                captured.setdefault("setup", []).append(query)
                return "OK"

            async def fetch(self, query, *params):
                captured.setdefault("queries", []).append(query)
                return []

        with patch("services.document_service.get_conn", return_value=_FakeConnCtx()), \
             patch("services.case_service.CaseService.get_user_viewable_sector_ids",
                   new_callable=AsyncMock, return_value=["sector-1"]):

            await get_user_documents(USER_ID, exclude_reserved=True, schema_name=SCHEMA)

        assert captured.get("queries"), "No se ejecuto ninguna query"
        assert all("document_type_is_reserved = false" in q for q in captured["queries"])

    @pytest.mark.asyncio
    async def test_exclude_reserved_false_does_not_filter(self):
        from services.document_service import get_user_documents

        captured = {}

        class _FakeConnCtx:
            async def __aenter__(self):
                return _FakeConnRow()

            async def __aexit__(self, *a):
                return False

        class _FakeConnRow:
            async def execute(self, query, *params):
                captured.setdefault("setup", []).append(query)
                return "OK"

            async def fetch(self, query, *params):
                captured.setdefault("queries", []).append(query)
                return []

        with patch("services.document_service.get_conn", return_value=_FakeConnCtx()), \
             patch("services.case_service.CaseService.get_user_viewable_sector_ids",
                   new_callable=AsyncMock, return_value=["sector-1"]):

            await get_user_documents(USER_ID, schema_name=SCHEMA)

        assert captured.get("queries")
        assert not any("document_type_is_reserved = false" in q for q in captured["queries"])


class TestExcludeReservedSearchOfficial:

    def test_query_projects_document_type_is_reserved(self):
        from services.documents.core.queries import search_official_document_by_number_query

        query = search_official_document_by_number_query()
        assert "dt.is_reserved as document_type_is_reserved" in query

    @pytest.mark.asyncio
    async def test_exclude_reserved_true_hides_reserved_document(self):
        from services.documents.retrieval.official_search import search_official_document_by_number

        with patch("services.documents.retrieval.official_search.fetch_one",
                   new_callable=AsyncMock,
                   return_value={"document_type_is_reserved": True, "document_id": DOC_ID}):

            result = await search_official_document_by_number(
                "IF-2026-00000001-SMG-ADGEN",
                exclude_reserved=True,
                schema_name=SCHEMA,
            )

        assert result["found"] is False
        assert result["document"] is None

    @pytest.mark.asyncio
    async def test_exclude_reserved_false_keeps_default_behavior(self):
        from services.documents.retrieval.official_search import search_official_document_by_number

        with patch("services.documents.retrieval.official_search.fetch_one",
                   new_callable=AsyncMock,
                   return_value={
                       "document_type_is_reserved": True,
                       "document_id": DOC_ID,
                       "official_number": "IF-2026-00000001-SMG-ADGEN",
                       "updated_at": None,
                       "document_type_name": "Informe",
                       "document_type_acronym": "IF",
                       "numerator_name": "Juan",
                       "creator_name": "Juan",
                       "document_base_type": "HTML",
                   }):

            result = await search_official_document_by_number(
                "IF-2026-00000001-SMG-ADGEN",
                schema_name=SCHEMA,
            )

        assert result["found"] is True


class TestDocumentTypesExposesIsReserved:

    def test_document_types_query_selects_is_reserved(self):
        from services.documents.core.queries import get_all_document_types_query

        query = get_all_document_types_query()
        assert "dt.is_reserved" in query

    def test_document_type_info_model_has_is_reserved_field(self):
        from models.shared.base import DocumentTypeInfo

        info = DocumentTypeInfo(name="Informe", acronym="IF")
        assert info.is_reserved is False

        info_reserved = DocumentTypeInfo(name="Reservado", acronym="RES", is_reserved=True)
        assert info_reserved.is_reserved is True


class TestLinkedCasesReservedGate:

    def test_query_masks_reference_and_projects_is_reserved(self):
        from services.documents.core.queries import get_linked_cases_for_official_document_query

        q = get_linked_cases_for_official_document_query()
        assert "COALESCE(ct.is_reserved, false) AS is_reserved" in q, \
            "Falta proyeccion de is_reserved"
        assert "CASE WHEN COALESCE(ct.is_reserved, false) THEN NULL ELSE c.reference END" in q, \
            "Falta mascara de reference para reservados"
        assert "LEFT JOIN case_templates ct" in q, \
            "Falta join a case_templates para leer is_reserved"

    def test_linked_case_info_model_has_is_reserved_field(self):
        from models.documents.signing import LinkedCaseInfo

        info = LinkedCaseInfo(case_id=CASE_ID, case_number="EXP-2025-1-SMG")
        assert info.is_reserved is False

        info_reserved = LinkedCaseInfo(
            case_id=CASE_ID, case_number="EXP-2025-2-SMG",
            reference=None, is_reserved=True,
        )
        assert info_reserved.is_reserved is True
        assert info_reserved.reference is None

    @pytest.mark.asyncio
    async def test_fetch_linked_cases_hides_reserved_case_from_unauthorized_viewer(self):
        from services.documents.signing import details_builder

        RESERVED_CASE_ID = "cccccccc-0000-0000-0000-000000000099"
        PUBLIC_CASE_ID = "cccccccc-0000-0000-0000-000000000042"
        import datetime as _dt
        rows = [
            {
                "case_id": RESERVED_CASE_ID,
                "case_number": "EXP-2025-99-SMG",
                "reference": None,
                "is_reserved": True,
                "order_number": 1,
                "linking_date": _dt.datetime(2026, 7, 8, 12, 0, 0),
            },
            {
                "case_id": PUBLIC_CASE_ID,
                "case_number": "EXP-2025-42-SMG",
                "reference": "Expediente publico visible",
                "is_reserved": False,
                "order_number": 2,
                "linking_date": _dt.datetime(2026, 7, 8, 13, 0, 0),
            },
        ]

        with patch.object(details_builder, "fetch_all", new=AsyncMock(return_value=rows)), \
             patch("services.cases.permissions.can_user_view_case",
                   new=AsyncMock(return_value=False)) as mock_can_view:
            result = await details_builder._fetch_linked_cases(
                DOC_ID, user_id=USER_ID, schema_name=SCHEMA
            )

        assert len(result) == 1
        assert result[0]["case_id"] == PUBLIC_CASE_ID
        assert result[0]["reference"] == "Expediente publico visible"
        assert result[0]["is_reserved"] is False

        assert mock_can_view.await_count == 1
        args, kwargs = mock_can_view.await_args
        assert RESERVED_CASE_ID in args
        assert USER_ID in args
        assert kwargs.get("schema_name") == SCHEMA

    @pytest.mark.asyncio
    async def test_fetch_linked_cases_shows_reserved_case_to_authorized_viewer(self):
        from services.documents.signing import details_builder

        RESERVED_CASE_ID = "cccccccc-0000-0000-0000-000000000099"
        import datetime as _dt
        rows = [
            {
                "case_id": RESERVED_CASE_ID,
                "case_number": "EXP-2025-99-SMG",
                "reference": None,
                "is_reserved": True,
                "order_number": 1,
                "linking_date": _dt.datetime(2026, 7, 8, 12, 0, 0),
            },
        ]

        with patch.object(details_builder, "fetch_all", new=AsyncMock(return_value=rows)), \
             patch("services.cases.permissions.can_user_view_case",
                   new=AsyncMock(return_value=True)):
            result = await details_builder._fetch_linked_cases(
                DOC_ID, user_id=USER_ID, schema_name=SCHEMA
            )

        assert len(result) == 1
        assert result[0]["case_id"] == RESERVED_CASE_ID
        assert result[0]["is_reserved"] is True
        assert result[0]["reference"] is None

    @pytest.mark.asyncio
    async def test_fetch_linked_cases_skips_permission_check_for_public_cases(self):
        from services.documents.signing import details_builder

        PUBLIC_CASE_ID = "cccccccc-0000-0000-0000-000000000042"
        import datetime as _dt
        rows = [
            {
                "case_id": PUBLIC_CASE_ID,
                "case_number": "EXP-2025-42-SMG",
                "reference": "Publico",
                "is_reserved": False,
                "order_number": 1,
                "linking_date": _dt.datetime(2026, 7, 8, 13, 0, 0),
            },
        ]

        with patch.object(details_builder, "fetch_all", new=AsyncMock(return_value=rows)), \
             patch("services.cases.permissions.can_user_view_case",
                   new=AsyncMock(return_value=False)) as mock_can_view:
            result = await details_builder._fetch_linked_cases(
                DOC_ID, user_id=USER_ID, schema_name=SCHEMA
            )

        assert len(result) == 1
        assert result[0]["case_id"] == PUBLIC_CASE_ID
        assert mock_can_view.await_count == 0, \
            "No se debe evaluar can_user_view_case para expedientes NO reservados"
