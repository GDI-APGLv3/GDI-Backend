
import os

import pytest
import requests

BASE_URL = "https://<your-backend-app>.fly.dev"
SCHEMA = "100_test"

TESTING_SECRET = os.getenv("GDI_TESTING_SECRET")

_MOTIVO_SIN_SECRETO = (
    "GDI_TESTING_SECRET no seteado: sin el header X-Testing-Secret (GDI-241) el "
    "backend de DEV responde 401 y estos tests no pueden afirmar nada sobre memos. "
    "Valor en 1Password / flyctl secrets -a <your-backend-app>."
)

USER_EMAIL = "test-user@example.com"
USER_ID = "a1000000-0000-0000-0000-000000000100"

NOTA_DOCUMENT_ID = "5da0ce43-fe11-47bb-b3fb-d18a707ae8e5"

MEMO_NONEXISTENT_ID = "00000000-0000-0000-0000-000000000000"

HEADERS = {
    "X-User-Email": USER_EMAIL,
    "X-Tenant-Schema": SCHEMA,
}

if TESTING_SECRET:
    HEADERS["X-Testing-Secret"] = TESTING_SECRET


@pytest.fixture(scope="session")
def testing_secret():
    if not TESTING_SECRET:
        pytest.skip(_MOTIVO_SIN_SECRETO)
    return TESTING_SECRET


@pytest.fixture(scope="session")
def session(testing_secret):
    s = requests.Session()
    s.headers.update(HEADERS)

    sanidad = s.get(f"{BASE_URL}/memos/unread-count", timeout=30)
    if sanidad.status_code == 401:
        pytest.fail(
            "El backend de DEV devolvio 401 con el header X-Testing-Secret puesto: "
            "el valor de GDI_TESTING_SECRET no coincide con el TESTING_SHARED_SECRET "
            f"de <your-backend-app> (GDI-241). Respuesta: {sanidad.text[:200]}"
        )
    return s


class TestMemosBandeja:

    def test_received_status_200(self, session):
        r = session.get(f"{BASE_URL}/memos/received", params={"page": 1, "page_size": 10})
        assert r.status_code == 200, f"Esperado 200, got {r.status_code}: {r.text[:200]}"

    def test_received_estructura_response(self, session):
        r = session.get(f"{BASE_URL}/memos/received", params={"page": 1, "page_size": 10})
        assert r.status_code == 200
        data = r.json()
        assert "memos" in data, f"Falta campo 'memos' en response: {data}"
        assert "pagination" in data, f"Falta campo 'pagination' en response: {data}"
        assert isinstance(data["memos"], list)
        pagination = data["pagination"]
        assert "page" in pagination
        assert "page_size" in pagination
        assert "total" in pagination
        assert "total_pages" in pagination

    def test_sent_status_200(self, session):
        r = session.get(f"{BASE_URL}/memos/sent", params={"page": 1, "page_size": 10})
        assert r.status_code == 200, f"Esperado 200, got {r.status_code}: {r.text[:200]}"

    def test_sent_estructura_response(self, session):
        r = session.get(f"{BASE_URL}/memos/sent", params={"page": 1, "page_size": 10})
        assert r.status_code == 200
        data = r.json()
        assert "memos" in data, f"Falta campo 'memos' en response: {data}"
        assert "pagination" in data, f"Falta campo 'pagination' en response: {data}"
        assert isinstance(data["memos"], list)

    def test_archived_status_200(self, session):
        r = session.get(f"{BASE_URL}/memos/archived", params={"page": 1, "page_size": 10})
        assert r.status_code == 200, f"Esperado 200, got {r.status_code}: {r.text[:200]}"

    def test_archived_estructura_response(self, session):
        r = session.get(f"{BASE_URL}/memos/archived", params={"page": 1, "page_size": 10})
        assert r.status_code == 200
        data = r.json()
        assert "memos" in data, f"Falta campo 'memos' en response: {data}"
        assert "pagination" in data, f"Falta campo 'pagination' en response: {data}"
        assert isinstance(data["memos"], list)

    def test_paginacion_page_invalida_422(self, session):
        r = session.get(f"{BASE_URL}/memos/received", params={"page": 0, "page_size": 10})
        assert r.status_code == 422, f"Esperado 422, got {r.status_code}: {r.text[:200]}"

    def test_paginacion_page_size_invalida_422(self, session):
        r = session.get(f"{BASE_URL}/memos/received", params={"page": 1, "page_size": 0})
        assert r.status_code == 422, f"Esperado 422, got {r.status_code}: {r.text[:200]}"

    def test_paginacion_page_size_grande_200(self, session):
        r = session.get(f"{BASE_URL}/memos/received", params={"page": 1, "page_size": 100})
        assert r.status_code == 200, f"Esperado 200, got {r.status_code}: {r.text[:200]}"


class TestMemosUnreadCount:

    def test_unread_count_status_200(self, session):
        r = session.get(f"{BASE_URL}/memos/unread-count")
        assert r.status_code == 200, f"Esperado 200, got {r.status_code}: {r.text[:200]}"

    def test_unread_count_estructura(self, session):
        r = session.get(f"{BASE_URL}/memos/unread-count")
        assert r.status_code == 200
        data = r.json()
        assert "unread_count" in data, f"Falta campo 'unread_count' en response: {data}"
        assert isinstance(data["unread_count"], int), f"unread_count debe ser int, es {type(data['unread_count'])}"
        assert data["unread_count"] >= 0, f"unread_count no puede ser negativo: {data['unread_count']}"


class TestMemoDetail:

    def test_memo_id_inexistente_404(self, session):
        r = session.get(f"{BASE_URL}/memos/{MEMO_NONEXISTENT_ID}")
        assert r.status_code == 404, f"Esperado 404, got {r.status_code}: {r.text[:200]}"

    def test_memo_id_de_nota_no_da_datos_memo(self, session):
        r = session.get(f"{BASE_URL}/memos/{NOTA_DOCUMENT_ID}")
        assert r.status_code in (403, 404), (
            f"GET /memos/{{nota_id}} para una NOTA deberia dar 403/404, "
            f"got {r.status_code}: {r.text[:200]}"
        )


class TestMemosArchive:

    def test_archive_id_inexistente_404(self, session):
        r = session.patch(
            f"{BASE_URL}/memos/{MEMO_NONEXISTENT_ID}/archive",
            json={"archived": True},
        )
        assert r.status_code == 404, f"Esperado 404, got {r.status_code}: {r.text[:200]}"

    def test_archive_body_invalido_422(self, session):
        r = session.patch(
            f"{BASE_URL}/memos/{MEMO_NONEXISTENT_ID}/archive",
            json={},
        )
        assert r.status_code == 422, f"Esperado 422, got {r.status_code}: {r.text[:200]}"


class TestMemosAuth:

    def test_sin_auth_no_200(self):
        r = requests.get(f"{BASE_URL}/memos/received", params={"page": 1, "page_size": 10})
        assert r.status_code != 200, (
            f"Request sin auth no deberia dar 200, got {r.status_code}"
        )

    def test_sin_auth_da_400_o_401(self):
        r = requests.get(f"{BASE_URL}/memos/received", params={"page": 1, "page_size": 10})
        assert r.status_code in (400, 401), (
            f"Sin auth esperado 400/401, got {r.status_code}: {r.text[:200]}"
        )

    def test_schema_invalido_400(self, testing_secret):
        r = requests.get(
            f"{BASE_URL}/memos/received",
            headers={
                "X-User-Email": USER_EMAIL,
                "X-Tenant-Schema": "schema_que_no_existe_para_nada",
                "X-Testing-Secret": testing_secret,
            },
            params={"page": 1, "page_size": 10},
        )
        assert r.status_code == 400, f"Esperado 400, got {r.status_code}: {r.text[:200]}"


class TestUserSearch:

    def test_user_search_status_200(self, session):
        r = session.get(f"{BASE_URL}/users/search", params={"q": "san", "limit": 5})
        assert r.status_code == 200, f"Esperado 200, got {r.status_code}: {r.text[:200]}"

    def test_user_search_estructura(self, session):
        r = session.get(f"{BASE_URL}/users/search", params={"q": "san", "limit": 5})
        assert r.status_code == 200
        data = r.json()
        assert "users" in data, f"Falta 'users' en response: {data}"
        assert isinstance(data["users"], list)
        assert "total_found" in data
        assert "search_query" in data

    def test_user_search_campos_por_usuario(self, session):
        r = session.get(f"{BASE_URL}/users/search", params={"q": "san", "limit": 5})
        assert r.status_code == 200
        data = r.json()
        if data["users"]:
            user = data["users"][0]
            assert "user_id" in user, f"Falta 'user_id' en usuario: {user}"
            assert "full_name" in user, f"Falta 'full_name' en usuario: {user}"
            assert "email" in user, f"Falta 'email' en usuario: {user}"
            assert "sector_acronym" in user, (
                f"Falta 'sector_acronym' en usuario (necesario para selector MEMO): {user}"
            )

    def test_user_search_retorna_resultados(self, session):
        r = session.get(f"{BASE_URL}/users/search", params={"q": "admin", "limit": 5})
        assert r.status_code == 200, f"Búsqueda de usuarios devolvió {r.status_code}: {r.text[:200]}"
        data = r.json()
        assert "users" in data, "Respuesta no contiene 'users'"
        for user in data["users"]:
            assert "user_id" in user, "Falta 'user_id' en resultado de búsqueda"
            assert "full_name" in user, "Falta 'full_name' en resultado de búsqueda"


class TestNotasRegression:

    def test_notes_received_status_200(self, session):
        r = session.get(f"{BASE_URL}/notes/received", params={"page": 1, "page_size": 5})
        assert r.status_code == 200, f"REGRESION: /notes/received falla con {r.status_code}: {r.text[:200]}"

    def test_notes_received_estructura(self, session):
        r = session.get(f"{BASE_URL}/notes/received", params={"page": 1, "page_size": 5})
        assert r.status_code == 200
        data = r.json()
        assert "notes" in data, f"REGRESION: Falta 'notes' en /notes/received: {data}"
        assert "pagination" in data
        assert isinstance(data["notes"], list)

    def test_notes_received_tiene_datos(self, session):
        r = session.get(f"{BASE_URL}/notes/received", params={"page": 1, "page_size": 5})
        assert r.status_code == 200
        data = r.json()
        assert len(data["notes"]) > 0, (
            "REGRESION: /notes/received no devuelve notas. "
            "Se esperan al menos 4 notas en schema 100_test."
        )

    def test_notes_received_campos_por_nota(self, session):
        r = session.get(f"{BASE_URL}/notes/received", params={"page": 1, "page_size": 5})
        assert r.status_code == 200
        data = r.json()
        if data["notes"]:
            nota = data["notes"][0]
            assert "document_id" in nota, f"Falta 'document_id' en nota: {nota.keys()}"
            assert "official_number" in nota, f"Falta 'official_number' en nota: {nota.keys()}"
            assert "document_type" in nota, f"Falta 'document_type' en nota: {nota.keys()}"
            assert nota["document_type"] == "NOTA", (
                f"REGRESION: /notes/received devuelve documentos tipo '{nota['document_type']}'"
                f" en vez de 'NOTA'"
            )

    def test_notes_sent_status_200(self, session):
        r = session.get(f"{BASE_URL}/notes/sent", params={"page": 1, "page_size": 5})
        assert r.status_code == 200, f"REGRESION: /notes/sent falla con {r.status_code}: {r.text[:200]}"

    def test_notes_sent_tiene_datos(self, session):
        r = session.get(f"{BASE_URL}/notes/sent", params={"page": 1, "page_size": 5})
        assert r.status_code == 200
        data = r.json()
        assert len(data["notes"]) > 0, (
            "REGRESION: /notes/sent no devuelve notas. "
            "Se esperan al menos 1 nota enviada en schema 100_test."
        )

    def test_notes_sent_no_contiene_memos(self, session):
        r = session.get(f"{BASE_URL}/notes/sent", params={"page": 1, "page_size": 20})
        assert r.status_code == 200
        data = r.json()
        for nota in data["notes"]:
            assert nota["document_type"] != "MEMO", (
                f"REGRESION/CONTAMINACION: /notes/sent devuelve un documento tipo MEMO "
                f"(id: {nota.get('document_id')}). Los MEMOs no deben aparecer en NOTAS."
            )
