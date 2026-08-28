from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from shared.exceptions import NotFoundError, ValidationError

TEST_SCHEMA = "100_test"
TEMPLATE_ID = "b0000000-0000-0000-0000-000000000001"
CITIZEN_ID = "c1000000-0000-0000-0000-000000000001"
USER_ID = "a1000000-0000-0000-0000-000000000001"
DEPT_ID = "d1000000-0000-0000-0000-000000000001"
SECTOR_ID = "51000000-0000-0000-0000-000000000001"
CASE_ID = "ca000000-0000-0000-0000-000000000001"


class TestValidateAndGetTemplate:
    async def test_incluye_filing_sector_id_y_creation_channel(self):
        from services.cases.validation import validate_and_get_template

        mock_conn = AsyncMock()
        mock_conn.fetchrow.return_value = {
            "id": TEMPLATE_ID,
            "filing_department_id": DEPT_ID,
            "filing_sector_id": SECTOR_ID,
            "creation_channel": "both",
            "type_name": "Habilitacion Comercial",
            "acronym": "HABI",
            "is_active": True,
        }

        result = await validate_and_get_template(mock_conn, TEMPLATE_ID)

        assert result["filing_sector_id"] == SECTOR_ID
        assert result["creation_channel"] == "both"

    async def test_template_no_encontrado_levanta_validation_error(self):
        from services.cases.validation import validate_and_get_template

        mock_conn = AsyncMock()
        mock_conn.fetchrow.return_value = None

        with pytest.raises(ValidationError):
            await validate_and_get_template(mock_conn, TEMPLATE_ID)


class TestValidateAndGetCitizen:
    async def test_citizen_existente_ok(self):
        from services.cases.validation import validate_and_get_citizen

        mock_conn = AsyncMock()
        mock_conn.fetchrow.return_value = {
            "id": CITIZEN_ID, "full_name": "Juan Perez", "estado": "validado",
        }

        result = await validate_and_get_citizen(mock_conn, CITIZEN_ID)

        assert result["citizen_id"] == CITIZEN_ID
        assert result["full_name"] == "Juan Perez"

    async def test_citizen_no_encontrado_levanta_not_found(self):
        from services.cases.validation import validate_and_get_citizen

        mock_conn = AsyncMock()
        mock_conn.fetchrow.return_value = None

        with pytest.raises(NotFoundError):
            await validate_and_get_citizen(mock_conn, CITIZEN_ID)


class TestValidateCreationChannelForCitizen:
    def test_channel_web_rechazado(self):
        from services.cases.validation import validate_creation_channel_for_citizen

        with pytest.raises(ValidationError):
            validate_creation_channel_for_citizen({"creation_channel": "web", "acronym": "EEVAR"})

    @pytest.mark.parametrize("channel", ["api", "both"])
    def test_channel_api_o_both_ok(self, channel):
        from services.cases.validation import validate_creation_channel_for_citizen

        validate_creation_channel_for_citizen({"creation_channel": channel, "acronym": "EEVAR"})


class TestCreateCaseCitizenBranch:
    def _mock_conn(self):
        mock_conn = MagicMock()
        mock_conn.fetch = AsyncMock(side_effect=[
            [{"dept_acronym": "SMG", "municipality_acronym": "MDF"}],
            [{"is_reserved": False}],
        ])
        mock_conn.execute = AsyncMock()
        mock_conn.fetchrow = AsyncMock(return_value={"next_sequence": 1})
        return mock_conn

    async def test_exactamente_uno_de_user_o_citizen(self):
        from services.cases.core import create_case

        mock_conn = self._mock_conn()
        with pytest.raises(ValueError):
            await create_case(
                mock_conn,
                case_template_id=TEMPLATE_ID,
                reference="ref",
                filing_department_id=DEPT_ID,
                created_by_user_id=None,
                created_by_citizen=None,
                owner_sector_id=SECTOR_ID,
                schema_name=TEST_SCHEMA,
            )

        mock_conn2 = self._mock_conn()
        with pytest.raises(ValueError):
            await create_case(
                mock_conn2,
                case_template_id=TEMPLATE_ID,
                reference="ref",
                filing_department_id=DEPT_ID,
                created_by_user_id=USER_ID,
                created_by_citizen=CITIZEN_ID,
                owner_sector_id=SECTOR_ID,
                schema_name=TEST_SCHEMA,
            )

    async def test_citizen_actor_usa_owner_sector_como_creator_sector(self):
        from services.cases.core import create_case

        mock_conn = self._mock_conn()

        with patch("services.cases.core.get_case_number_format", return_value="EE-{sequence}-SMG"):
            await create_case(
                mock_conn,
                case_template_id=TEMPLATE_ID,
                reference="Solicitud de habilitacion",
                filing_department_id=DEPT_ID,
                created_by_citizen=CITIZEN_ID,
                owner_sector_id=SECTOR_ID,
                schema_name=TEST_SCHEMA,
            )

        movement_calls = [c for c in mock_conn.execute.call_args_list if "INSERT INTO case_movements" in c.args[0]]
        assert len(movement_calls) == 1
        args = movement_calls[0].args
        assert CITIZEN_ID in args
        assert args.count(SECTOR_ID) >= 2

        case_calls = [c for c in mock_conn.execute.call_args_list if "INSERT INTO cases" in c.args[0]]
        assert len(case_calls) == 1
        assert CITIZEN_ID in case_calls[0].args

    async def test_citizen_actor_con_template_reservado_no_auto_asigna_responsable(self):
        from services.cases.core import create_case

        mock_conn = MagicMock()
        mock_conn.fetch = AsyncMock(side_effect=[
            [{"dept_acronym": "SMG", "municipality_acronym": "MDF"}],
            [{"is_reserved": True}],
        ])
        mock_conn.execute = AsyncMock()
        mock_conn.fetchrow = AsyncMock(return_value={"next_sequence": 1})

        with patch("services.cases.core.get_case_number_format", return_value="EE-{sequence}-SMG"), \
             patch("services.cases.responsibles.add_responsible", new_callable=AsyncMock) as mock_add:
            await create_case(
                mock_conn,
                case_template_id=TEMPLATE_ID,
                reference="Solicitud de habilitacion",
                filing_department_id=DEPT_ID,
                created_by_citizen=CITIZEN_ID,
                owner_sector_id=SECTOR_ID,
                schema_name=TEST_SCHEMA,
            )

        mock_add.assert_not_called()


def _tx_ctx(mock_conn):
    @asynccontextmanager
    async def _ctx(**kwargs):
        yield mock_conn
    return _ctx


class TestCreateCaseWithCoverServiceCitizenBranch:
    async def test_exactamente_uno_de_user_id_o_citizen_id(self):
        from services.cases.creation import create_case_with_cover_service

        with pytest.raises(ValidationError):
            await create_case_with_cover_service(
                TEMPLATE_ID, "ref", user_id=None, citizen_id=None, schema_name=TEST_SCHEMA,
            )

        with pytest.raises(ValidationError):
            await create_case_with_cover_service(
                TEMPLATE_ID, "ref", user_id=USER_ID, citizen_id=CITIZEN_ID, schema_name=TEST_SCHEMA,
            )

    async def test_channel_web_rechaza_creacion_citizen(self):
        from services.cases.creation import create_case_with_cover_service

        mock_conn = AsyncMock()

        template_data = {
            "id": TEMPLATE_ID, "filing_department_id": DEPT_ID, "filing_sector_id": SECTOR_ID,
            "creation_channel": "web", "type_name": "Expediente Varios", "acronym": "EEVAR",
            "is_active": True,
        }

        with patch("services.cases.creation.transaction", side_effect=lambda **kw: _tx_ctx(mock_conn)()), \
             patch("services.cases.creation.validate_and_get_template", new_callable=AsyncMock) as mock_tpl:
            mock_tpl.return_value = template_data
            with pytest.raises(ValidationError):
                await create_case_with_cover_service(
                    TEMPLATE_ID, "ref", citizen_id=CITIZEN_ID, schema_name=TEST_SCHEMA,
                )

    async def test_momento1_y_momento2_ok_para_actor_citizen(self):
        from services.cases.creation import create_case_with_cover_service

        mock_conn = AsyncMock()

        template_data = {
            "id": TEMPLATE_ID, "filing_department_id": DEPT_ID, "filing_sector_id": SECTOR_ID,
            "creation_channel": "api", "type_name": "Habilitacion Comercial", "acronym": "HABI",
            "is_active": True,
        }
        citizen_data = {"citizen_id": CITIZEN_ID, "full_name": "Juan Perez", "estado": "validado"}
        case_result = {"case_id": CASE_ID, "case_number": "EE-2026-00000001-MUNI-INT", "reference": "ref"}
        cover_result = {"success": True, "document_id": "doc-1", "official_number": "CAEX-2026-00000001-MUNI-TAD"}

        with patch("services.cases.creation.transaction", side_effect=lambda **kw: _tx_ctx(mock_conn)()), \
             patch("services.cases.creation.validate_and_get_template", new_callable=AsyncMock) as mock_tpl, \
             patch("services.cases.creation.validate_and_get_citizen", new_callable=AsyncMock) as mock_cit, \
             patch("services.cases.creation.validate_owner_sector_belongs_to_department", new_callable=AsyncMock) as mock_val_sector, \
             patch("services.case_service.CaseService.create_case", new_callable=AsyncMock) as mock_create_case, \
             patch("services.cases.creation.share_case_with_citizen", new_callable=AsyncMock) as mock_share, \
             patch("services.cases.creation.create_case_cover", new_callable=AsyncMock) as mock_cover, \
             patch("services.cases.creation.execute", new_callable=AsyncMock) as mock_execute:
            mock_tpl.return_value = template_data
            mock_cit.return_value = citizen_data
            mock_create_case.return_value = case_result
            mock_cover.return_value = cover_result

            result = await create_case_with_cover_service(
                TEMPLATE_ID, "Solicitud de habilitacion", citizen_id=CITIZEN_ID, schema_name=TEST_SCHEMA,
            )

        mock_val_sector.assert_awaited_once_with(mock_conn, SECTOR_ID, DEPT_ID)

        mock_create_case.assert_awaited_once()
        _, kwargs = mock_create_case.call_args
        assert kwargs["created_by_citizen"] == CITIZEN_ID
        assert kwargs["created_by_user_id"] is None
        assert kwargs["creator_sector_id"] is None
        assert kwargs["owner_sector_id"] == SECTOR_ID

        mock_share.assert_awaited_once_with(
            case_id=CASE_ID, citizen_id=CITIZEN_ID, shared_by=None,
            schema_name=TEST_SCHEMA, conn=mock_conn,
        )

        mock_cover.assert_awaited_once()
        _, cover_kwargs = mock_cover.call_args
        assert cover_kwargs["citizen_id"] == CITIZEN_ID
        assert cover_kwargs["user_id"] is None

        assert result["cover"] == cover_result
        assert result["created_by"] == "Juan Perez"

    async def test_owner_sector_id_explicito_tiene_prioridad_sobre_filing_sector_id(self):
        from services.cases.creation import create_case_with_cover_service

        mock_conn = AsyncMock()
        explicit_sector_id = "52000000-0000-0000-0000-000000000002"

        template_data = {
            "id": TEMPLATE_ID, "filing_department_id": DEPT_ID, "filing_sector_id": SECTOR_ID,
            "creation_channel": "api", "type_name": "Habilitacion Comercial", "acronym": "HABI",
            "is_active": True,
        }
        citizen_data = {"citizen_id": CITIZEN_ID, "full_name": "Juan Perez", "estado": "validado"}
        case_result = {"case_id": CASE_ID, "case_number": "EE-2026-00000001-MUNI-INT", "reference": "ref"}

        with patch("services.cases.creation.transaction", side_effect=lambda **kw: _tx_ctx(mock_conn)()), \
             patch("services.cases.creation.validate_and_get_template", new_callable=AsyncMock) as mock_tpl, \
             patch("services.cases.creation.validate_and_get_citizen", new_callable=AsyncMock) as mock_cit, \
             patch("services.cases.creation.validate_owner_sector_belongs_to_department", new_callable=AsyncMock) as mock_val_sector, \
             patch("services.case_service.CaseService.create_case", new_callable=AsyncMock) as mock_create_case, \
             patch("services.cases.creation.share_case_with_citizen", new_callable=AsyncMock), \
             patch("services.cases.creation.create_case_cover", new_callable=AsyncMock) as mock_cover, \
             patch("services.cases.creation.execute", new_callable=AsyncMock):
            mock_tpl.return_value = template_data
            mock_cit.return_value = citizen_data
            mock_create_case.return_value = case_result
            mock_cover.return_value = {"success": True, "document_id": "doc-1", "official_number": "CAEX-1"}

            await create_case_with_cover_service(
                TEMPLATE_ID, "ref", citizen_id=CITIZEN_ID,
                owner_sector_id=explicit_sector_id, schema_name=TEST_SCHEMA,
            )

        _, kwargs = mock_create_case.call_args
        assert kwargs["owner_sector_id"] == explicit_sector_id

        mock_val_sector.assert_not_awaited()


class TestCreateCaseWithCoverServiceUserBranch:

    def _run(self, *, creator_sector_id, owner_sector_id=None):
        from services.cases.creation import create_case_with_cover_service

        mock_conn = AsyncMock()

        template_data = {
            "id": TEMPLATE_ID, "filing_department_id": DEPT_ID, "filing_sector_id": SECTOR_ID,
            "creation_channel": "both", "type_name": "Expediente Varios", "acronym": "EEVAR",
            "is_active": True,
        }
        user_data = {
            "user_id": USER_ID, "full_name": "Agente Interno",
            "sector_id": creator_sector_id, "department_id": DEPT_ID,
        }
        case_result = {"case_id": CASE_ID, "case_number": "EE-2026-00000002-MUNI-INT", "reference": "ref"}

        async def _go():
            with patch("services.cases.creation.transaction", side_effect=lambda **kw: _tx_ctx(mock_conn)()), \
                 patch("services.cases.creation.validate_and_get_template", new_callable=AsyncMock) as mock_tpl, \
                 patch("services.cases.creation.validate_and_get_user", new_callable=AsyncMock) as mock_usr, \
                 patch("services.cases.creation.validate_owner_sector_belongs_to_department", new_callable=AsyncMock) as mock_val_sector, \
                 patch("services.case_service.CaseService.create_case", new_callable=AsyncMock) as mock_create_case, \
                 patch("services.cases.creation.create_case_cover", new_callable=AsyncMock) as mock_cover, \
                 patch("services.cases.creation.execute", new_callable=AsyncMock):
                mock_tpl.return_value = template_data
                mock_usr.return_value = user_data
                mock_create_case.return_value = case_result
                mock_cover.return_value = {"success": True, "document_id": "doc-1", "official_number": "CAEX-1"}

                await create_case_with_cover_service(
                    TEMPLATE_ID, "ref", user_id=USER_ID,
                    owner_sector_id=owner_sector_id, schema_name=TEST_SCHEMA,
                )

                _, kwargs = mock_create_case.call_args
                return kwargs, mock_val_sector

        return _go()

    async def test_default_es_el_sector_del_usuario_creador_no_el_de_radicacion(self):
        creator_sector_id = "51000000-0000-0000-0000-0000000000aa"

        kwargs, mock_val_sector = await self._run(creator_sector_id=creator_sector_id)

        assert kwargs["owner_sector_id"] == creator_sector_id
        assert kwargs["owner_sector_id"] != SECTOR_ID
        assert kwargs["creator_sector_id"] == creator_sector_id
        assert kwargs["created_by_user_id"] == USER_ID
        assert kwargs["created_by_citizen"] is None

        mock_val_sector.assert_not_awaited()

    async def test_owner_sector_id_explicito_sigue_ganando(self):
        explicit_sector_id = "51000000-0000-0000-0000-0000000000bb"

        kwargs, mock_val_sector = await self._run(
            creator_sector_id="51000000-0000-0000-0000-0000000000aa",
            owner_sector_id=explicit_sector_id,
        )

        assert kwargs["owner_sector_id"] == explicit_sector_id
        mock_val_sector.assert_not_awaited()

    async def test_usuario_sin_sector_cae_al_filing_sector_id_y_se_valida(self):
        kwargs, mock_val_sector = await self._run(creator_sector_id=None)

        assert kwargs["owner_sector_id"] == SECTOR_ID
        mock_val_sector.assert_awaited_once()
