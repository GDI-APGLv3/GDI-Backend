

def test_mock_sector_permission(mock_sector_permission):
    assert mock_sector_permission.sector_id == "51000000-0000-0000-0000-000000000001"
    assert mock_sector_permission.sector_acronym == "PRIV"
    assert mock_sector_permission.department_id == "d1000000-0000-0000-0000-000000000001"
    assert mock_sector_permission.department_name == "Intendencia"
    assert mock_sector_permission.department_acronym == "INTE"
    assert mock_sector_permission.can_view is True
    assert mock_sector_permission.can_edit is True
    assert mock_sector_permission.is_primary is True


def test_test_headers(test_headers):
    assert "X-Tenant-Schema" in test_headers
    assert "X-User-ID" in test_headers
    assert test_headers["X-Tenant-Schema"] == "100_test"
    assert test_headers["X-User-ID"] == "a1000000-0000-0000-0000-000000000001"


def test_mock_authenticated_user_new(mock_authenticated_user_new):
    assert mock_authenticated_user_new.user_id == "a1000000-0000-0000-0000-000000000001"
    assert mock_authenticated_user_new.auth_id == "auth0|test123"
    assert mock_authenticated_user_new.email == "mrodriguez@munitest.com"
    assert mock_authenticated_user_new.full_name == "Maria Rodriguez"
    assert len(mock_authenticated_user_new.permissions) == 1

    from models.schemas import SectorPermission
    assert isinstance(mock_authenticated_user_new.permissions[0], SectorPermission)
    assert mock_authenticated_user_new.permissions[0].sector_id == "51000000-0000-0000-0000-000000000001"


def test_mock_authenticated_user(mock_authenticated_user):
    from models.schemas import AuthenticatedUser
    assert isinstance(mock_authenticated_user, AuthenticatedUser)
    assert mock_authenticated_user.user_id == "a1000000-0000-0000-0000-000000000001"
    assert mock_authenticated_user.email == "test.user@municipalidad.test"
    assert mock_authenticated_user.full_name == "Usuario Test"
