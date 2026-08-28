from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from shared.exceptions import ConflictError, NotFoundError, ValidationError

TEST_SCHEMA = "100_test"
TEMPLATE_ID = "b0000000-0000-0000-0000-000000000001"
CITIZEN_ID = "c1000000-0000-0000-0000-000000000001"
OTHER_CITIZEN_ID = "c1000000-0000-0000-0000-000000000009"
USER_ID = "a1000000-0000-0000-0000-000000000001"
DEPT_ID = "d1000000-0000-0000-0000-000000000001"
SECTOR_ID = "51000000-0000-0000-0000-000000000001"
CREATOR_SECTOR_ID = "51000000-0000-0000-0000-0000000000aa"
CASE_ID = "ca000000-0000-0000-0000-000000000001"

TEMPLATE_DATA = {
    "id": TEMPLATE_ID, "filing_department_id": DEPT_ID, "filing_sector_id": SECTOR_ID,
    "creation_channel": "both", "type_name": "Expediente Varios", "acronym": "EEVAR",
    "is_active": True,
}
USER_DATA = {
    "user_id": USER_ID, "full_name": "Agente Interno",
    "sector_id": CREATOR_SECTOR_ID, "department_id": DEPT_ID,
}
CASE_RESULT = {"case_id": CASE_ID, "case_number": "EE-2026-00000003-MUNI-INT", "reference": "ref"}


def _tx_ctx(mock_conn):
    @asynccontextmanager
    async def _ctx(**kwargs):
        yield mock_conn
    return _ctx


@asynccontextmanager
async def _internal_flow(*, citizen_estado="validado", citizen_found=True, share_side_effect=None):
    mock_conn = AsyncMock()
    mocks = MagicMock()
    mocks.conn = mock_conn

    with patch("services.cases.creation.transaction", side_effect=lambda **kw: _tx_ctx(mock_conn)()), \
         patch("services.cases.creation.validate_and_get_template", new_callable=AsyncMock) as mock_tpl, \
         patch("services.cases.creation.validate_and_get_user", new_callable=AsyncMock) as mock_usr, \
         patch("services.cases.creation.validate_and_get_citizen", new_callable=AsyncMock) as mock_cit, \
         patch("services.cases.creation.validate_owner_sector_belongs_to_department", new_callable=AsyncMock), \
         patch("services.case_service.CaseService.create_case", new_callable=AsyncMock) as mock_create_case, \
         patch("services.cases.creation.share_case_with_citizen", new_callable=AsyncMock) as mock_share, \
         patch("services.cases.creation.create_case_cover", new_callable=AsyncMock) as mock_cover, \
         patch("services.cases.creation.execute", new_callable=AsyncMock), \
         patch("services.cases.creation.send_alert_mail", new_callable=AsyncMock, create=True):
        mock_tpl.return_value = TEMPLATE_DATA
        mock_usr.return_value = USER_DATA
        if citizen_found:
            mock_cit.return_value = {
                "citizen_id": CITIZEN_ID, "full_name": "Juan Perez", "estado": citizen_estado,
            }
        else:
            mock_cit.side_effect = NotFoundError(f"Ciudadano {CITIZEN_ID} no encontrado en el sistema")
        mock_create_case.return_value = CASE_RESULT
        mock_share.side_effect = share_side_effect
        mock_cover.return_value = {
            "success": True, "document_id": "doc-1", "official_number": "CAEX-2026-00000003-MUNI-INT",
        }

        mocks.template = mock_tpl
        mocks.user = mock_usr
        mocks.citizen = mock_cit
        mocks.create_case = mock_create_case
        mocks.share = mock_share
        mocks.cover = mock_cover
        yield mocks


class TestCreateCaseConIniciadorCiudadano:
    async def test_persiste_columna_y_comparte_con_shared_by_none(self):
        from services.cases.creation import create_case_with_cover_service

        async with _internal_flow() as m:
            result = await create_case_with_cover_service(
                TEMPLATE_ID, "Reclamo por bache en la vereda",
                user_id=USER_ID, initiator_citizen_id=CITIZEN_ID, schema_name=TEST_SCHEMA,
            )

        _, kwargs = m.create_case.call_args
        assert kwargs["initiator_citizen_id"] == CITIZEN_ID
        assert kwargs["created_by_user_id"] == USER_ID
        assert kwargs["created_by_citizen"] is None
        assert kwargs["owner_sector_id"] == CREATOR_SECTOR_ID
        assert kwargs["creator_sector_id"] == CREATOR_SECTOR_ID

        m.share.assert_awaited_once_with(
            case_id=CASE_ID, citizen_id=CITIZEN_ID, shared_by=None,
            schema_name=TEST_SCHEMA, conn=m.conn,
        )

        _, cover_kwargs = m.cover.call_args
        assert cover_kwargs["user_id"] == USER_ID
        assert cover_kwargs["citizen_id"] is None

        assert result["created_by"] == "Agente Interno"

    async def test_sin_iniciador_no_hay_share(self):
        from services.cases.creation import create_case_with_cover_service

        async with _internal_flow() as m:
            await create_case_with_cover_service(
                TEMPLATE_ID, "Expediente interno de siempre",
                user_id=USER_ID, schema_name=TEST_SCHEMA,
            )

        m.share.assert_not_awaited()
        _, kwargs = m.create_case.call_args
        assert kwargs["initiator_citizen_id"] is None

    async def test_si_el_share_falla_no_se_llega_a_la_caex(self):
        from services.cases.creation import create_case_with_cover_service

        async with _internal_flow(share_side_effect=RuntimeError("share caido")) as m:
            with pytest.raises(RuntimeError):
                await create_case_with_cover_service(
                    TEMPLATE_ID, "Reclamo por bache en la vereda",
                    user_id=USER_ID, initiator_citizen_id=CITIZEN_ID, schema_name=TEST_SCHEMA,
                )

        m.cover.assert_not_awaited()


class TestValidacionDelIniciador:
    async def test_ciudadano_bloqueado_rechazado(self):
        from services.cases.creation import create_case_with_cover_service

        async with _internal_flow(citizen_estado="bloqueado") as m:
            with pytest.raises(ValidationError):
                await create_case_with_cover_service(
                    TEMPLATE_ID, "Reclamo por bache en la vereda",
                    user_id=USER_ID, initiator_citizen_id=CITIZEN_ID, schema_name=TEST_SCHEMA,
                )

        m.create_case.assert_not_awaited()

    @pytest.mark.parametrize("estado", ["validado", "pendiente"])
    async def test_validado_y_pendiente_son_elegibles(self, estado):
        from services.cases.creation import create_case_with_cover_service

        async with _internal_flow(citizen_estado=estado) as m:
            await create_case_with_cover_service(
                TEMPLATE_ID, "Reclamo por bache en la vereda",
                user_id=USER_ID, initiator_citizen_id=CITIZEN_ID, schema_name=TEST_SCHEMA,
            )

        m.share.assert_awaited_once()

    async def test_ciudadano_inexistente_levanta_not_found(self):
        from services.cases.creation import create_case_with_cover_service

        async with _internal_flow(citizen_found=False) as m:
            with pytest.raises(NotFoundError):
                await create_case_with_cover_service(
                    TEMPLATE_ID, "Reclamo por bache en la vereda",
                    user_id=USER_ID, initiator_citizen_id=CITIZEN_ID, schema_name=TEST_SCHEMA,
                )

        m.create_case.assert_not_awaited()

    async def test_iniciador_no_convive_con_actor_ciudadano(self):
        from services.cases.creation import create_case_with_cover_service

        with pytest.raises(ValidationError):
            await create_case_with_cover_service(
                TEMPLATE_ID, "ref", citizen_id=CITIZEN_ID,
                initiator_citizen_id=OTHER_CITIZEN_ID, schema_name=TEST_SCHEMA,
            )


class TestCreateCaseInsertaInitiatorCitizen:
    def _mock_conn(self):
        mock_conn = MagicMock()
        mock_conn.fetch = AsyncMock(side_effect=[
            [{"dept_acronym": "SMG", "municipality_acronym": "MDF"}],
            [{"is_reserved": False}],
        ])
        mock_conn.execute = AsyncMock()
        mock_conn.fetchrow = AsyncMock(return_value={"next_sequence": 3})
        return mock_conn

    async def test_initiator_citizen_id_viaja_en_el_insert(self):
        from services.cases.core import create_case

        mock_conn = self._mock_conn()

        with patch("services.cases.core.get_case_number_format", return_value="EE-{sequence}-SMG"):
            await create_case(
                mock_conn,
                case_template_id=TEMPLATE_ID,
                reference="Reclamo por bache en la vereda",
                filing_department_id=DEPT_ID,
                created_by_user_id=USER_ID,
                initiator_citizen_id=CITIZEN_ID,
                owner_sector_id=CREATOR_SECTOR_ID,
                schema_name=TEST_SCHEMA,
            )

        case_calls = [c for c in mock_conn.execute.call_args_list if "INSERT INTO cases" in c.args[0]]
        assert len(case_calls) == 1
        assert "initiator_citizen_id" in case_calls[0].args[0]
        assert CITIZEN_ID in case_calls[0].args
        assert USER_ID in case_calls[0].args

        movement_calls = [c for c in mock_conn.execute.call_args_list if "INSERT INTO case_movements" in c.args[0]]
        assert len(movement_calls) == 1
        assert CITIZEN_ID not in movement_calls[0].args

    async def test_initiator_no_entra_en_la_exclusion_mutua_del_actor(self):
        from services.cases.core import create_case

        with pytest.raises(ValueError):
            await create_case(
                self._mock_conn(),
                case_template_id=TEMPLATE_ID,
                reference="ref",
                filing_department_id=DEPT_ID,
                created_by_user_id=None,
                created_by_citizen=None,
                initiator_citizen_id=CITIZEN_ID,
                owner_sector_id=SECTOR_ID,
                schema_name=TEST_SCHEMA,
            )


class TestUnshareGuardIniciador:
    async def _unshare(self, row):
        from services.cases.citizen_shares import unshare_case_from_citizen

        with patch("services.cases.citizen_shares.fetch_all", new_callable=AsyncMock) as mock_fetch, \
             patch("services.cases.citizen_shares._record_citizen_share_movement", new_callable=AsyncMock):
            mock_fetch.side_effect = [[row], [{"id": "share-1"}]]
            await unshare_case_from_citizen(
                CASE_ID, CITIZEN_ID, removed_by=USER_ID, schema_name=TEST_SCHEMA,
            )

    async def test_quitar_al_iniciador_gdi166_devuelve_conflict(self):
        with pytest.raises(ConflictError):
            await self._unshare({"created_by_citizen": None, "initiator_citizen_id": CITIZEN_ID})

    async def test_quitar_al_creador_tad_sigue_devolviendo_conflict(self):
        with pytest.raises(ConflictError):
            await self._unshare({"created_by_citizen": CITIZEN_ID, "initiator_citizen_id": None})

    async def test_quitar_a_un_compartido_comun_sigue_permitido(self):
        await self._unshare({"created_by_citizen": None, "initiator_citizen_id": OTHER_CITIZEN_ID})

    async def test_sin_iniciador_se_puede_quitar(self):
        await self._unshare({"created_by_citizen": None, "initiator_citizen_id": None})
