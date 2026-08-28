import pytest
from shared.exceptions import ValidationError
from config.constants import SYSTEM_TEST_USER_UUID


SYSTEM_TEST_UUID = "00000000-0000-0000-0000-000074657374"
NORMAL_USER_UUID = "aaaaaaaa-0000-0000-0000-000000000001"


class TestConstant:

    def test_system_test_uuid_constant_exists(self):
        from config import constants
        assert hasattr(constants, "SYSTEM_TEST_USER_UUID"), (
            "SYSTEM_TEST_USER_UUID no existe en config.constants"
        )

    def test_system_test_uuid_correct_value(self):
        assert SYSTEM_TEST_USER_UUID.lower() == SYSTEM_TEST_UUID.lower()


class TestValidatorBlocksSystemTestUser:

    def test_system_test_user_blocked_as_signer(self):
        from services.documents.core.validator import DocumentValidator

        signer = {
            "user_id": SYSTEM_TEST_UUID,
            "is_numerator": True,
        }
        with pytest.raises(ValidationError) as exc_info:
            DocumentValidator._validate_single_signer(signer, 1)

        msg = str(exc_info.value).lower()
        assert "sistema" in msg or "test" in msg or "público" in msg

    def test_normal_user_is_not_blocked(self):
        from services.documents.core.validator import DocumentValidator

        signer = {
            "user_id": NORMAL_USER_UUID,
            "is_numerator": True,
        }
        DocumentValidator._validate_single_signer(signer, 1)

    def test_system_test_user_blocked_case_insensitive(self):
        from services.documents.core.validator import DocumentValidator

        signer = {
            "user_id": SYSTEM_TEST_UUID.upper(),
            "is_numerator": True,
        }
        with pytest.raises(ValidationError):
            DocumentValidator._validate_single_signer(signer, 1)

    def test_system_test_not_numerator_still_blocked(self):
        from services.documents.core.validator import DocumentValidator

        signer = {
            "user_id": SYSTEM_TEST_UUID,
            "is_numerator": False,
        }
        with pytest.raises(ValidationError):
            DocumentValidator._validate_single_signer(signer, 1)


class TestValidatorC4InternalException:

    def test_internal_flag_bypasses_block(self):
        from services.documents.core.validator import DocumentValidator

        signer = {
            "user_id": SYSTEM_TEST_UUID,
            "is_numerator": True,
        }
        DocumentValidator._validate_single_signer(signer, 1, internal=True)

    def test_internal_false_still_blocks(self):
        from services.documents.core.validator import DocumentValidator

        signer = {
            "user_id": SYSTEM_TEST_UUID,
            "is_numerator": True,
        }
        with pytest.raises(ValidationError):
            DocumentValidator._validate_single_signer(signer, 1, internal=False)

    def test_validate_signers_list_propagates_internal(self):
        from services.documents.core.validator import DocumentValidator

        signers = [
            {"user_id": SYSTEM_TEST_UUID, "is_numerator": True},
        ]

        with pytest.raises(ValidationError):
            DocumentValidator._validate_signers_list(signers, internal=False)

        DocumentValidator._validate_signers_list(signers, internal=True)

    def test_validate_update_data_propagates_internal(self):
        from services.documents.core.validator import DocumentValidator

        signers = [
            {"user_id": SYSTEM_TEST_UUID, "is_numerator": True},
        ]

        with pytest.raises(ValidationError):
            DocumentValidator.validate_update_data(
                reference=None, content=None, signers=signers
            )

        DocumentValidator.validate_update_data(
            reference=None, content=None, signers=signers, internal=True
        )


class TestQueryFilters:

    def test_search_users_excludes_system_test(self):
        from services.users.queries import search_users_by_name_query

        sql = search_users_by_name_query()
        assert SYSTEM_TEST_UUID in sql, (
            "search_users_by_name_query no contiene el UUID de Sistema TEST para excluirlo"
        )
        assert "!=" in sql, "search_users_by_name_query no excluye el UUID (operador != faltante)"

    def test_count_users_excludes_system_test(self):
        from services.users.queries import count_users_by_name_query

        sql = count_users_by_name_query()
        assert SYSTEM_TEST_UUID in sql
        assert "!=" in sql

    def test_list_all_users_excludes_system_test(self):
        from services.users.queries import list_all_users_query

        sql = list_all_users_query()
        assert SYSTEM_TEST_UUID in sql
        assert "!=" in sql


class TestRealPathValidateDocumentSigners:

    @pytest.mark.asyncio
    async def test_public_path_blocks_system_test_user(self):
        from unittest.mock import AsyncMock, patch
        from shared.validation import validate_document_signers

        signers = [{"user_id": SYSTEM_TEST_UUID, "is_numerator": True}]
        with patch("shared.validation.validate_user_id", AsyncMock(return_value=None)):
            error = await validate_document_signers(signers, schema_name="100_test")
        assert error is not None and "Firmante 1" in error

    @pytest.mark.asyncio
    async def test_internal_c4_allows_system_test_user(self):
        from unittest.mock import AsyncMock, patch
        from shared.validation import validate_document_signers

        signers = [{"user_id": SYSTEM_TEST_UUID, "is_numerator": True}]
        with patch("shared.validation.validate_user_id", AsyncMock(return_value=None)):
            error = await validate_document_signers(
                signers, schema_name="100_test", internal=True
            )
        assert error is None

    @pytest.mark.asyncio
    async def test_normal_user_unaffected(self):
        from unittest.mock import AsyncMock, patch
        from shared.validation import validate_document_signers

        signers = [{"user_id": NORMAL_USER_UUID, "is_numerator": True}]
        with patch("shared.validation.validate_user_id", AsyncMock(return_value=None)):
            error = await validate_document_signers(signers, schema_name="100_test")
        assert error is None

    def test_assignable_users_query_excludes_system_test(self):
        from services.case_queries import get_assignable_users_query

        for with_sector in (False, True):
            sql = get_assignable_users_query(with_sector=with_sector)
            assert SYSTEM_TEST_UUID in sql, (
                f"get_assignable_users_query(with_sector={with_sector}) "
                "no excluye el UUID de Sistema TEST"
            )
