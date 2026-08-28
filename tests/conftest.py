import os

os.environ.setdefault("TESTING_SHARED_SECRET", "secreto-de-tests-gdi241")
TESTING_SECRET = os.environ["TESTING_SHARED_SECRET"]

import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from unittest.mock import MagicMock, AsyncMock, patch
import asyncio
from typing import Dict, Any

from main import app
from models.schemas import AuthenticatedUser, SectorPermission

MOCK_USER_AUTH = AuthenticatedUser(
    user_id="a1000000-0000-0000-0000-000000000001",
    auth_id="auth0|test_user_123",
    email="test.user@municipalidad.test",
    full_name="Usuario Test",
    permissions=[
        SectorPermission(
            sector_id="51000000-0000-0000-0000-000000000001",
            sector_acronym="PRIV",
            department_id="d1000000-0000-0000-0000-000000000001",
            department_name="Intendencia",
            department_acronym="INTE",
            can_view=True,
            can_edit=True,
            is_primary=True
        )
    ]
)

MOCK_USER_DB = {
    "user_id": "550e8400-e29b-41d4-a716-446655440000",
    "auth_id": "auth0|test_user_123",
    "full_name": "Usuario Test",
    "email": "test.user@municipalidad.test",
    "sector_id": "770e8400-e29b-41d4-a716-446655440001",
    "estado": 1
}

MOCK_CASE = {
    "id": "ef102207-86c4-415a-883b-088b51ee5d45",
    "case_number": "EE-2025-000001-SMG-OBPU",
    "reference": "Expediente de prueba para testing",
    "status": "active",
    "created_at": "2025-10-01T14:53:05.470279+00:00",
    "case_template_id": "3da979f0-3d01-4ba5-8b10-cd4258351440",
    "created_by_user_id": "550e8400-e29b-41d4-a716-446655440000",
    "owner_department_id": "3b5b8451-1f23-4662-a2b1-aa70e46b6594",
    "owner_sector_id": "770e8400-e29b-41d4-a716-446655440001"
}

MOCK_CASE_TEMPLATE = {
    "id": "3da979f0-3d01-4ba5-8b10-cd4258351440",
    "type_name": "Licitación Pública",
    "description": "Procesos de licitación para compras y obras",
    "acronym": "LIC",
    "filing_department_id": "3b5b8451-1f23-4662-a2b1-aa70e46b6594",
    "is_active": True
}

MOCK_MOVEMENT = {
    "id": "mov123456-86c4-415a-883b-088b51ee5d45",
    "case_id": "ef102207-86c4-415a-883b-088b51ee5d45",
    "type": "creation",
    "reason": "Creación del expediente",
    "created_at": "2025-10-01T14:53:05.470279+00:00",
    "user_id": "550e8400-e29b-41d4-a716-446655440000",
    "creator_sector_id": "770e8400-e29b-41d4-a716-446655440001",
    "is_active": True
}

MOCK_DOCUMENT = {
    "id": "doc123456-86c4-415a-883b-088b51ee5d45",
    "reference": "Documento de prueba",
    "document_type": "official",
    "official_number": "OF-2025-000001-SMG",
    "signed_at": "2025-10-01T14:53:05.470279+00:00"
}

MOCK_SECTOR = {
    "sector_id": "770e8400-e29b-41d4-a716-446655440001",
    "department_id": "3b5b8451-1f23-4662-a2b1-aa70e46b6594",
    "acronym": "OBPU",
    "is_active": True
}

MOCK_DEPARTMENT = {
    "department_id": "3b5b8451-1f23-4662-a2b1-aa70e46b6594",
    "name": "Obras Públicas",
    "acronym": "OBPU",
    "is_active": True
}

@pytest.fixture
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()

@pytest.fixture(autouse=True)
def _reset_dts_rate_limiter():
    from services.shared import dts_rate_limiter
    with dts_rate_limiter._fallback_lock:
        dts_rate_limiter._fallback_window["minute"] = None
        dts_rate_limiter._fallback_window["count"] = 0
    yield

@pytest_asyncio.fixture
async def client():
    import database as db_module
    from database import init_pool, close_pool
    from shared.tenant_validation import clear_all_cache

    if db_module.pool is not None:
        try:
            await close_pool()
        except Exception:
            pass

    pool_ok = False
    try:
        await init_pool()
        clear_all_cache()
        pool_ok = True
    except Exception:
        pass

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac

    if pool_ok:
        try:
            await close_pool()
        except Exception:
            pass

@pytest.fixture
def mock_auth_headers():
    return {"Authorization": "Bearer test_jwt_token"}

@pytest.fixture
def mock_authenticated_user():
    return MOCK_USER_AUTH

@pytest.fixture
def mock_db_user():
    return MOCK_USER_DB

@pytest.fixture
def mock_case_data():
    return MOCK_CASE

@pytest.fixture
def mock_case_template():
    return MOCK_CASE_TEMPLATE

@pytest.fixture
def mock_movement_data():
    return MOCK_MOVEMENT

@pytest.fixture
def mock_document_data():
    return MOCK_DOCUMENT

@pytest.fixture
def mock_sector_data():
    return MOCK_SECTOR

@pytest.fixture
def mock_department_data():
    return MOCK_DEPARTMENT

@pytest.fixture
def mock_db_connection():
    with patch('database.fetch_all', new_callable=AsyncMock) as mock_fetch_all, \
         patch('database.fetch_one', new_callable=AsyncMock) as mock_fetch_one, \
         patch('database.fetch_val', new_callable=AsyncMock) as mock_fetch_val, \
         patch('database.execute', new_callable=AsyncMock) as mock_execute:
        mock_fetch_all.return_value = []
        mock_fetch_one.return_value = None
        mock_fetch_val.return_value = None
        mock_execute.return_value = "UPDATE 0"
        yield {
            'fetch_all': mock_fetch_all,
            'fetch_one': mock_fetch_one,
            'fetch_val': mock_fetch_val,
            'execute': mock_execute,
        }

@pytest.fixture
def mock_case_service():
    with patch('services.case_service.CaseService') as mock_service:
        yield mock_service

@pytest.fixture
def mock_user_service():
    with patch('services.user_service.UserService') as mock_service:
        yield mock_service

@pytest.fixture
def mock_auth():
    with patch('auth.get_current_user') as mock_auth:
        mock_auth.return_value = MOCK_USER_AUTH
        yield mock_auth

def create_mock_response(data: Dict[str, Any], status_code: int = 200):
    mock_response = MagicMock()
    mock_response.status_code = status_code
    mock_response.json.return_value = data
    return mock_response

def assert_response_success(response, expected_keys=None):
    assert response.status_code == 200
    data = response.json()
    assert "success" in data
    assert data["success"] is True
    
    if expected_keys:
        for key in expected_keys:
            assert key in data

def assert_response_error(response, expected_status_code, expected_message=None):
    assert response.status_code == expected_status_code

    if expected_message:
        data = response.json()
        assert "detail" in data
        assert expected_message in data["detail"]


@pytest.fixture
def mock_sector_permission():
    from models.schemas import SectorPermission
    return SectorPermission(
        sector_id="51000000-0000-0000-0000-000000000001",
        sector_acronym="PRIV",
        department_id="d1000000-0000-0000-0000-000000000001",
        department_name="Intendencia",
        department_acronym="INTE",
        can_view=True,
        can_edit=True,
        is_primary=True
    )

@pytest.fixture
def test_headers():
    return {
        "X-Tenant-Schema": "100_test",
        "X-User-ID": "a1000000-0000-0000-0000-000000000001",
        "X-Testing-Secret": TESTING_SECRET,
    }

@pytest.fixture
def mock_authenticated_user_new(mock_sector_permission):
    from models.schemas import AuthenticatedUser
    return AuthenticatedUser(
        user_id="a1000000-0000-0000-0000-000000000001",
        auth_id="auth0|test123",
        email="mrodriguez@munitest.com",
        full_name="Maria Rodriguez",
        permissions=[mock_sector_permission]
    )