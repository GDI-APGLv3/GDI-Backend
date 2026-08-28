from unittest.mock import AsyncMock, patch

import pytest

from shared.exceptions import ConflictError, NotFoundError

TEST_SCHEMA = "100_test"
TEST_CASE_ID = "ca000000-0000-0000-0000-000000000001"
TEST_CITIZEN_ID = "c1000000-0000-0000-0000-000000000001"
TEST_OTHER_CITIZEN_ID = "c2000000-0000-0000-0000-000000000002"
TEST_USER_ID = "a1000000-0000-0000-0000-000000000001"
TEST_SHARE_ID = "cc000000-0000-0000-0000-000000000001"
TEST_SECTOR_ID = "se000000-0000-0000-0000-000000000001"

CITIZEN_NAME_ROW = {"full_name": "Juan Perez", "country_id": "20111111112"}
ADMIN_SECTOR_ROW = {"admin_sector_id": TEST_SECTOR_ID}
USER_SECTOR_ROW = {"sector_id": TEST_SECTOR_ID, "full_name": "Ana Admin"}


def _route_fetch(
    query,
    *,
    citizen_exists=None,
    active_share=None,
    creator_citizen=None,
    citizen_name=CITIZEN_NAME_ROW,
    admin_sector=ADMIN_SECTOR_ROW,
    user_sector=USER_SECTOR_ROW,
):
    if "FROM case_citizen_shares" in query:
        return [active_share] if active_share else []
    if "initiator_citizen_id FROM cases" in query:
        return [creator_citizen] if creator_citizen else []
    if "full_name, country_id FROM citizens" in query:
        return [citizen_name] if citizen_name else []
    if "FROM citizens WHERE id = $1" in query:
        return [citizen_exists] if citizen_exists else []
    if "admin_sector_id FROM case_movements" in query:
        return [admin_sector] if admin_sector else []
    if "sector_id, full_name FROM users" in query:
        return [user_sector] if user_sector else []
    return []


class TestShareCaseWithCitizen:
    async def test_citizen_no_existe_levanta_not_found(self):
        from services.cases.citizen_shares import share_case_with_citizen

        with patch("services.cases.citizen_shares.fetch_all", new_callable=AsyncMock) as mock_fetch:
            mock_fetch.return_value = []
            with pytest.raises(NotFoundError):
                await share_case_with_citizen(
                    TEST_CASE_ID, TEST_CITIZEN_ID, TEST_USER_ID, schema_name=TEST_SCHEMA,
                )

    async def test_citizen_bloqueado_levanta_conflict_sin_insertar(self):
        from services.cases.citizen_shares import share_case_with_citizen

        with patch("services.cases.citizen_shares.fetch_all", new_callable=AsyncMock) as mock_fetch, \
             patch("services.cases.citizen_shares.execute", new_callable=AsyncMock) as mock_execute:
            mock_fetch.return_value = [{"id": TEST_CITIZEN_ID, "estado": "bloqueado"}]
            with pytest.raises(ConflictError):
                await share_case_with_citizen(
                    TEST_CASE_ID, TEST_CITIZEN_ID, TEST_USER_ID, schema_name=TEST_SCHEMA,
                )
            mock_execute.assert_not_called()

    async def test_share_ya_activo_es_noop_idempotente(self):
        from services.cases.citizen_shares import share_case_with_citizen

        async def _fetch_side_effect(query, *args, **kwargs):
            if "citizens" in query and "case_citizen_shares" not in query:
                return [{"id": TEST_CITIZEN_ID, "estado": "validado"}]
            return [{"id": TEST_SHARE_ID}]

        with patch("services.cases.citizen_shares.fetch_all", new_callable=AsyncMock) as mock_fetch, \
             patch("services.cases.citizen_shares.execute", new_callable=AsyncMock) as mock_execute:
            mock_fetch.side_effect = _fetch_side_effect

            result = await share_case_with_citizen(
                TEST_CASE_ID, TEST_CITIZEN_ID, TEST_USER_ID, schema_name=TEST_SCHEMA,
            )

            assert result["already_shared"] is True
            assert result["id"] == TEST_SHARE_ID
            mock_execute.assert_not_called()

    async def test_share_nuevo_inserta_y_devuelve_already_shared_false(self):
        from services.cases.citizen_shares import share_case_with_citizen

        async def _fetch_side_effect(query, *args, **kwargs):
            if query.startswith("SELECT id, estado FROM citizens"):
                return [{"id": TEST_CITIZEN_ID, "estado": "validado"}]
            if "FROM case_citizen_shares" in query:
                return []
            return _route_fetch(query)

        with patch("services.cases.citizen_shares.fetch_all", new_callable=AsyncMock) as mock_fetch, \
             patch("services.cases.citizen_shares.execute", new_callable=AsyncMock) as mock_execute:
            mock_fetch.side_effect = _fetch_side_effect

            result = await share_case_with_citizen(
                TEST_CASE_ID, TEST_CITIZEN_ID, TEST_USER_ID, schema_name=TEST_SCHEMA,
            )

            assert result["already_shared"] is False
            assert result["case_id"] == TEST_CASE_ID
            assert result["citizen_id"] == TEST_CITIZEN_ID
            assert mock_execute.await_count == 2
            _, kwargs = mock_execute.call_args_list[0]
            assert kwargs["auth_source"] == "jwt"
            assert kwargs["user_id"] == TEST_USER_ID

    async def test_share_automatico_tad_usa_auth_source_tad(self):
        from services.cases.citizen_shares import share_case_with_citizen

        async def _fetch_side_effect(query, *args, **kwargs):
            if query.startswith("SELECT id, estado FROM citizens"):
                return [{"id": TEST_CITIZEN_ID, "estado": "validado"}]
            if "FROM case_citizen_shares" in query:
                return []
            return _route_fetch(query)

        with patch("services.cases.citizen_shares.fetch_all", new_callable=AsyncMock) as mock_fetch, \
             patch("services.cases.citizen_shares.execute", new_callable=AsyncMock) as mock_execute:
            mock_fetch.side_effect = _fetch_side_effect

            result = await share_case_with_citizen(
                TEST_CASE_ID, TEST_CITIZEN_ID, None, schema_name=TEST_SCHEMA,
            )

            assert result["already_shared"] is False
            _, kwargs = mock_execute.call_args_list[0]
            assert kwargs["auth_source"] == "tad"
            assert kwargs["user_id"] is None
            movement_args, movement_kwargs = mock_execute.call_args_list[1]
            assert movement_kwargs["auth_source"] == "tad"
            assert movement_kwargs["user_id"] == TEST_CITIZEN_ID

    async def test_share_con_conn_participa_de_transaccion_activa(self):
        from services.cases.citizen_shares import share_case_with_citizen

        mock_conn = AsyncMock()

        async def _conn_fetch_side_effect(query, *args, **kwargs):
            if query.startswith("SELECT id, estado FROM citizens"):
                return [{"id": TEST_CITIZEN_ID, "estado": "validado"}]
            if "FROM case_citizen_shares" in query:
                return []
            return _route_fetch(query)

        mock_conn.fetch.side_effect = _conn_fetch_side_effect

        with patch("services.cases.citizen_shares.fetch_all", new_callable=AsyncMock) as mock_fetch, \
             patch("services.cases.citizen_shares.execute", new_callable=AsyncMock) as mock_execute:
            result = await share_case_with_citizen(
                TEST_CASE_ID, TEST_CITIZEN_ID, None, schema_name=TEST_SCHEMA, conn=mock_conn,
            )

            assert result["already_shared"] is False
            assert mock_conn.execute.await_count == 2
            mock_fetch.assert_not_called()
            mock_execute.assert_not_called()


class TestUnshareCaseFromCitizen:
    async def test_guard_no_quita_al_creador_del_expediente(self):
        from services.cases.citizen_shares import unshare_case_from_citizen

        async def _fetch_side_effect(query, *args, **kwargs):
            if "initiator_citizen_id FROM cases" in query:
                return [{"created_by_citizen": TEST_CITIZEN_ID, "initiator_citizen_id": None}]
            return []

        with patch("services.cases.citizen_shares.fetch_all", new_callable=AsyncMock) as mock_fetch:
            mock_fetch.side_effect = _fetch_side_effect
            with pytest.raises(ConflictError):
                await unshare_case_from_citizen(
                    TEST_CASE_ID, TEST_CITIZEN_ID, TEST_USER_ID, schema_name=TEST_SCHEMA,
                )
            assert mock_fetch.await_count == 1

    async def test_guard_permite_quitar_a_otro_ciudadano_no_creador(self):
        from services.cases.citizen_shares import unshare_case_from_citizen

        async def _fetch_side_effect(query, *args, **kwargs):
            if "initiator_citizen_id FROM cases" in query:
                return [{"created_by_citizen": TEST_OTHER_CITIZEN_ID, "initiator_citizen_id": None}]
            if "UPDATE case_citizen_shares" in query:
                return [{"id": TEST_SHARE_ID}]
            return _route_fetch(query)

        with patch("services.cases.citizen_shares.fetch_all", new_callable=AsyncMock) as mock_fetch, \
             patch("services.cases.citizen_shares.execute", new_callable=AsyncMock) as mock_execute:
            mock_fetch.side_effect = _fetch_side_effect
            await unshare_case_from_citizen(
                TEST_CASE_ID, TEST_CITIZEN_ID, TEST_USER_ID, schema_name=TEST_SCHEMA,
            )
            mock_execute.assert_awaited_once()

    async def test_sin_share_activo_levanta_not_found(self):
        from services.cases.citizen_shares import unshare_case_from_citizen

        async def _fetch_side_effect(query, *args, **kwargs):
            if "initiator_citizen_id FROM cases" in query:
                return []
            return []

        with patch("services.cases.citizen_shares.fetch_all", new_callable=AsyncMock) as mock_fetch:
            mock_fetch.side_effect = _fetch_side_effect
            with pytest.raises(NotFoundError):
                await unshare_case_from_citizen(
                    TEST_CASE_ID, TEST_CITIZEN_ID, TEST_USER_ID, schema_name=TEST_SCHEMA,
                )

    async def test_unshare_ok_registra_movement_de_historial(self):
        from services.cases.citizen_shares import unshare_case_from_citizen

        async def _fetch_side_effect(query, *args, **kwargs):
            if "initiator_citizen_id FROM cases" in query:
                return [{"created_by_citizen": None, "initiator_citizen_id": None}]
            if "UPDATE case_citizen_shares" in query:
                return [{"id": TEST_SHARE_ID}]
            return _route_fetch(query)

        with patch("services.cases.citizen_shares.fetch_all", new_callable=AsyncMock) as mock_fetch, \
             patch("services.cases.citizen_shares.execute", new_callable=AsyncMock) as mock_execute:
            mock_fetch.side_effect = _fetch_side_effect
            await unshare_case_from_citizen(
                TEST_CASE_ID, TEST_CITIZEN_ID, TEST_USER_ID, schema_name=TEST_SCHEMA,
            )
            mock_execute.assert_awaited_once()
            _, kwargs = mock_execute.call_args
            assert kwargs["auth_source"] == "jwt"
            assert kwargs["user_id"] == TEST_USER_ID


class TestListings:
    async def test_list_shares_of_case_mapea_filas(self):
        from services.cases.citizen_shares import list_shares_of_case

        row = {
            "id": TEST_SHARE_ID,
            "citizen_id": TEST_CITIZEN_ID,
            "full_name": "Juan Perez",
            "country_id": "20111111112",
            "citizen_estado": "validado",
            "shared_by": None,
            "shared_at": None,
        }
        with patch("services.cases.citizen_shares.fetch_all", new_callable=AsyncMock) as mock_fetch:
            mock_fetch.return_value = [row]
            result = await list_shares_of_case(TEST_CASE_ID, schema_name=TEST_SCHEMA)

        assert len(result) == 1
        assert result[0]["citizen_id"] == TEST_CITIZEN_ID
        assert result[0]["shared_by"] is None

    async def test_list_cases_shared_with_citizen_mapea_filas(self):
        from services.cases.citizen_shares import list_cases_shared_with_citizen

        row = {
            "case_id": TEST_CASE_ID,
            "case_number": "EE-2026-00000001-MUNI-INT",
            "reference": "Solicitud de habilitacion",
            "status": "active",
            "template_name": "Habilitacion Comercial",
            "template_acronym": "HABI",
            "shared_at": None,
        }
        with patch("services.cases.citizen_shares.fetch_all", new_callable=AsyncMock) as mock_fetch:
            mock_fetch.return_value = [row]
            result = await list_cases_shared_with_citizen(TEST_CITIZEN_ID, schema_name=TEST_SCHEMA)

        assert len(result) == 1
        assert result[0]["case_id"] == TEST_CASE_ID
        assert result[0]["case_number"] == "EE-2026-00000001-MUNI-INT"

    async def test_can_citizen_access_case_true_false(self):
        from services.cases.citizen_shares import can_citizen_access_case

        with patch("services.cases.citizen_shares.fetch_all", new_callable=AsyncMock) as mock_fetch:
            mock_fetch.return_value = [{"id": TEST_SHARE_ID}]
            assert await can_citizen_access_case(TEST_CASE_ID, TEST_CITIZEN_ID, schema_name=TEST_SCHEMA) is True

            mock_fetch.return_value = []
            assert await can_citizen_access_case(TEST_CASE_ID, TEST_CITIZEN_ID, schema_name=TEST_SCHEMA) is False


class TestCanUserManageCitizenShares:
    async def test_delega_en_can_user_edit_case(self):
        from services.cases.citizen_shares import can_user_manage_citizen_shares

        with patch("services.cases.citizen_shares.can_user_edit_case", new_callable=AsyncMock) as mock_edit:
            mock_edit.return_value = True
            assert await can_user_manage_citizen_shares(TEST_CASE_ID, TEST_USER_ID, schema_name=TEST_SCHEMA) is True
            mock_edit.assert_awaited_once_with(TEST_CASE_ID, TEST_USER_ID, schema_name=TEST_SCHEMA)

            mock_edit.return_value = False
            assert await can_user_manage_citizen_shares(TEST_CASE_ID, TEST_USER_ID, schema_name=TEST_SCHEMA) is False


class TestSearchCitizens:
    async def test_busca_por_nombre_o_country_id_case_insensitive(self):
        from services.citizens.search import search_citizens

        row = {"id": TEST_CITIZEN_ID, "full_name": "Juan Perez", "country_id": "20111111112", "estado": "validado"}
        with patch("services.citizens.search.fetch_all", new_callable=AsyncMock) as mock_fetch:
            mock_fetch.return_value = [row]
            result = await search_citizens("juan", schema_name=TEST_SCHEMA)

        assert len(result) == 1
        assert result[0]["full_name"] == "Juan Perez"
        args, kwargs = mock_fetch.call_args
        assert kwargs["schema_name"] == TEST_SCHEMA
        assert "%juan%" in args

    async def test_sin_resultados_devuelve_lista_vacia(self):
        from services.citizens.search import search_citizens

        with patch("services.citizens.search.fetch_all", new_callable=AsyncMock) as mock_fetch:
            mock_fetch.return_value = []
            result = await search_citizens("no-existe", schema_name=TEST_SCHEMA)

        assert result == []
