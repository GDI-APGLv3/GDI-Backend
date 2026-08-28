
import inspect
from unittest.mock import AsyncMock, MagicMock, patch

SCHEMA = "100_test"
USER_ID = "aaaaaaaa-0000-0000-0000-000000000001"
SECTOR_ID = "51000000-0000-0000-0000-000000000001"


class TestGlobalSearchFlagNoAplicaAlListado:

    async def _capture_is_global(self, search_filter):
        from services.cases import retrieval

        captured = {}
        real_builder = retrieval._build_where_conditions

        def spy(*args, **kwargs):
            captured.setdefault("is_global_search", kwargs.get("is_global_search"))
            return real_builder(*args, **kwargs)

        with patch.object(retrieval, "_build_where_conditions", side_effect=spy), \
             patch.object(retrieval, "_get_user_sector_ids", new_callable=AsyncMock) as mock_sectors, \
             patch.object(retrieval, "get_cached", new_callable=AsyncMock) as mock_cached:
            mock_sectors.return_value = [SECTOR_ID]
            mock_cached.return_value = 0

            await retrieval.get_cases_by_user(
                user_id=USER_ID,
                search_filter=search_filter,
                schema_name=SCHEMA,
                search_flags={"can_global_search_cases": True},
            )

        return captured.get("is_global_search")

    async def test_sin_busqueda_no_es_global_aunque_tenga_el_flag(self):
        assert await self._capture_is_global(None) is False

    async def test_busqueda_de_texto_libre_no_es_global(self):
        assert await self._capture_is_global("panaderia") is False

    async def test_busqueda_por_numero_de_expediente_si_es_global(self):
        assert await self._capture_is_global("EE-2026-00000001-MUNI") is True


class TestWhereConditionsSectorGate:

    def test_rama_no_reservada_filtra_por_sector_cuando_no_es_global(self):
        from services.cases.retrieval import _build_where_conditions

        where_sql, _ = _build_where_conditions(
            None, None, [], None, None, None,
            is_global_search=False,
            sector_param=1, user_id_param=2, param_start=3,
        )
        flat = " ".join(where_sql.split())
        assert "NOT ct.is_reserved AND (TRUE)" not in flat
        assert "cm.assigned_sector_id = ANY($1::uuid[])" in flat
        assert "cm.admin_sector_id = ANY($1::uuid[])" in flat


class TestCountsRespetanReservados:

    def test_source_aplica_gate_de_reservados(self):
        from endpoints.cases import counts

        source = inspect.getsource(counts)
        assert "build_reserved_or_exists" in source
        assert "NOT ct.is_reserved" in source
        assert "JOIN case_templates ct ON ct.id = c.case_template_id" in source

    async def test_las_4_queries_llevan_el_gate(self):
        from endpoints.cases import counts
        from services.cases.reserved_predicate import RESERVED_BRANCH_SUBSTRINGS

        queries = []

        async def fake_fetch_all(query, *args, **kwargs):
            queries.append(query)
            return [{"total": 0}]

        request = MagicMock()
        request.state.tenant_user_id = USER_ID

        with patch.object(counts, "fetch_all", side_effect=fake_fetch_all), \
             patch.object(counts, "get_authenticated_user", new_callable=AsyncMock) as mock_user:
            mock_user.return_value = USER_ID

            result = await counts.get_case_counts(
                request=request,
                current_user=MagicMock(),
                schema_name=SCHEMA,
            )

        assert len(queries) == 4, "Se esperaban las 4 queries de badges"
        for q in queries:
            assert "case_templates ct" in q
            assert "NOT ct.is_reserved" in q
            for substring in RESERVED_BRANCH_SUBSTRINGS:
                assert substring in q, f"Falta {substring!r} en el gate de reservados"

        assert (result.asignado, result.admin, result.actuante, result.favoritos) == (0, 0, 0, 0)
