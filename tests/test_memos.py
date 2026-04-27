"""
Tests del modulo MEMOS - endpoints REST contra DEV.

Testea todos los endpoints de memos:
- GET /memos/received
- GET /memos/sent
- GET /memos/archived
- GET /memos/unread-count
- GET /memos/{document_id}
- PATCH /memos/{document_id}/archive

Incluye test de regresion de NOTAS (verifica que no se rompio nada).

Ejecucion:
    pytest tests/test_memos.py -v
    pytest tests/test_memos.py -v -k "regression"  # solo regresion
"""

import os

import pytest
import requests

BASE_URL = os.getenv("GDI_TEST_BASE_URL", "http://localhost:8000")
SCHEMA = "100_test"

USER_EMAIL = "test-user@example.com"
USER_ID = "a1000000-0000-0000-0000-000000000100"

# ID de una NOTA existente en el schema 100_test (para test de isolation)
NOTA_DOCUMENT_ID = "5da0ce43-fe11-47bb-b3fb-d18a707ae8e5"

# UUID valido pero inexistente en memos
MEMO_NONEXISTENT_ID = "00000000-0000-0000-0000-000000000000"

HEADERS = {
    "X-User-Email": USER_EMAIL,
    "X-Tenant-Schema": SCHEMA,
}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def session():
    """requests.Session reutilizable para todos los tests."""
    s = requests.Session()
    s.headers.update(HEADERS)
    return s


# ---------------------------------------------------------------------------
# Tests de bandeja
# ---------------------------------------------------------------------------


class TestMemosBandeja:
    """Tests de los tres endpoints de bandeja de memos."""

    def test_received_status_200(self, session):
        """GET /memos/received debe responder 200."""
        r = session.get(f"{BASE_URL}/memos/received", params={"page": 1, "page_size": 10})
        assert r.status_code == 200, f"Esperado 200, got {r.status_code}: {r.text[:200]}"

    def test_received_estructura_response(self, session):
        """GET /memos/received debe tener memos[] y pagination{}."""
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
        """GET /memos/sent debe responder 200."""
        r = session.get(f"{BASE_URL}/memos/sent", params={"page": 1, "page_size": 10})
        assert r.status_code == 200, f"Esperado 200, got {r.status_code}: {r.text[:200]}"

    def test_sent_estructura_response(self, session):
        """GET /memos/sent debe tener memos[] y pagination{}."""
        r = session.get(f"{BASE_URL}/memos/sent", params={"page": 1, "page_size": 10})
        assert r.status_code == 200
        data = r.json()
        assert "memos" in data, f"Falta campo 'memos' en response: {data}"
        assert "pagination" in data, f"Falta campo 'pagination' en response: {data}"
        assert isinstance(data["memos"], list)

    def test_archived_status_200(self, session):
        """GET /memos/archived debe responder 200."""
        r = session.get(f"{BASE_URL}/memos/archived", params={"page": 1, "page_size": 10})
        assert r.status_code == 200, f"Esperado 200, got {r.status_code}: {r.text[:200]}"

    def test_archived_estructura_response(self, session):
        """GET /memos/archived debe tener memos[] y pagination{}."""
        r = session.get(f"{BASE_URL}/memos/archived", params={"page": 1, "page_size": 10})
        assert r.status_code == 200
        data = r.json()
        assert "memos" in data, f"Falta campo 'memos' en response: {data}"
        assert "pagination" in data, f"Falta campo 'pagination' en response: {data}"
        assert isinstance(data["memos"], list)

    def test_paginacion_page_invalida_422(self, session):
        """GET /memos/received con page=0 debe dar 422."""
        r = session.get(f"{BASE_URL}/memos/received", params={"page": 0, "page_size": 10})
        assert r.status_code == 422, f"Esperado 422, got {r.status_code}: {r.text[:200]}"

    def test_paginacion_page_size_invalida_422(self, session):
        """GET /memos/received con page_size=0 debe dar 422."""
        r = session.get(f"{BASE_URL}/memos/received", params={"page": 1, "page_size": 0})
        assert r.status_code == 422, f"Esperado 422, got {r.status_code}: {r.text[:200]}"

    def test_paginacion_page_size_grande_200(self, session):
        """GET /memos/received con page_size=100 debe dar 200 (sin limite explicito en spec)."""
        r = session.get(f"{BASE_URL}/memos/received", params={"page": 1, "page_size": 100})
        assert r.status_code == 200, f"Esperado 200, got {r.status_code}: {r.text[:200]}"


# ---------------------------------------------------------------------------
# Tests de unread-count
# ---------------------------------------------------------------------------


class TestMemosUnreadCount:
    """Tests del endpoint de contador de no leidos."""

    def test_unread_count_status_200(self, session):
        """GET /memos/unread-count debe responder 200."""
        r = session.get(f"{BASE_URL}/memos/unread-count")
        assert r.status_code == 200, f"Esperado 200, got {r.status_code}: {r.text[:200]}"

    def test_unread_count_estructura(self, session):
        """GET /memos/unread-count debe tener campo unread_count como entero >= 0."""
        r = session.get(f"{BASE_URL}/memos/unread-count")
        assert r.status_code == 200
        data = r.json()
        assert "unread_count" in data, f"Falta campo 'unread_count' en response: {data}"
        assert isinstance(data["unread_count"], int), f"unread_count debe ser int, es {type(data['unread_count'])}"
        assert data["unread_count"] >= 0, f"unread_count no puede ser negativo: {data['unread_count']}"


# ---------------------------------------------------------------------------
# Tests de detalle de memo
# ---------------------------------------------------------------------------


class TestMemoDetail:
    """Tests del endpoint de detalle de un memo especifico."""

    def test_memo_id_inexistente_404(self, session):
        """GET /memos/{id} con UUID inexistente debe dar 404."""
        r = session.get(f"{BASE_URL}/memos/{MEMO_NONEXISTENT_ID}")
        assert r.status_code == 404, f"Esperado 404, got {r.status_code}: {r.text[:200]}"

    def test_memo_id_de_nota_no_da_datos_memo(self, session):
        """GET /memos/{id} con document_id de una NOTA no debe devolver datos.

        Si el usuario NO es emisor ni destinatario del documento segun memo_recipients,
        debe dar 403 (access denied) o 404. Nunca 200 con datos de la NOTA.
        """
        r = session.get(f"{BASE_URL}/memos/{NOTA_DOCUMENT_ID}")
        # 403 = el endpoint existe, pero el doc no es un memo del que el user forma parte
        # 404 = no existe en memo_recipients directamente
        assert r.status_code in (403, 404), (
            f"GET /memos/{{nota_id}} para una NOTA deberia dar 403/404, "
            f"got {r.status_code}: {r.text[:200]}"
        )

    def test_memo_id_malformado_no_500(self, session):
        """GET /memos/{id} con UUID malformado NO deberia dar 500.

        BUG CONOCIDO: actualmente da 500 en lugar de 422.
        Registrado para seguimiento. Este test FALLARA hasta que se corrija.
        """
        r = session.get(f"{BASE_URL}/memos/no-es-un-uuid-valido")
        # El comportamiento correcto es 422 (validation error de FastAPI)
        # Actualmente hay un bug que devuelve 500
        assert r.status_code == 422, (
            f"BUG: GET /memos/{{uuid_malformado}} devuelve {r.status_code} en vez de 422. "
            f"Respuesta: {r.text[:200]}"
        )


# ---------------------------------------------------------------------------
# Tests de archivar/desarchivar
# ---------------------------------------------------------------------------


class TestMemosArchive:
    """Tests del endpoint PATCH /memos/{id}/archive."""

    def test_archive_id_inexistente_404(self, session):
        """PATCH /memos/{id}/archive con UUID inexistente debe dar 404."""
        r = session.patch(
            f"{BASE_URL}/memos/{MEMO_NONEXISTENT_ID}/archive",
            json={"archived": True},
        )
        assert r.status_code == 404, f"Esperado 404, got {r.status_code}: {r.text[:200]}"

    def test_archive_body_invalido_422(self, session):
        """PATCH /memos/{id}/archive sin campo 'archived' debe dar 422."""
        r = session.patch(
            f"{BASE_URL}/memos/{MEMO_NONEXISTENT_ID}/archive",
            json={},
        )
        assert r.status_code == 422, f"Esperado 422, got {r.status_code}: {r.text[:200]}"

    def test_archive_uuid_malformado_no_500(self, session):
        """PATCH /memos/{id}/archive con UUID malformado NO deberia dar 500.

        BUG CONOCIDO: actualmente da 500 en lugar de 422.
        Registrado para seguimiento. Este test FALLARA hasta que se corrija.
        """
        r = session.patch(
            f"{BASE_URL}/memos/no-es-un-uuid-valido/archive",
            json={"archived": True},
        )
        assert r.status_code == 422, (
            f"BUG: PATCH /memos/{{uuid_malformado}}/archive devuelve {r.status_code} "
            f"en vez de 422. Respuesta: {r.text[:200]}"
        )


# ---------------------------------------------------------------------------
# Tests de autenticacion y schema
# ---------------------------------------------------------------------------


class TestMemosAuth:
    """Tests de autenticacion y validacion de schema."""

    def test_sin_auth_no_200(self):
        """Request sin header de auth no debe dar 200."""
        r = requests.get(f"{BASE_URL}/memos/received", params={"page": 1, "page_size": 10})
        assert r.status_code != 200, (
            f"Request sin auth no deberia dar 200, got {r.status_code}"
        )

    def test_sin_auth_da_400_o_401(self):
        """Request sin header de auth debe dar 400 o 401."""
        r = requests.get(f"{BASE_URL}/memos/received", params={"page": 1, "page_size": 10})
        assert r.status_code in (400, 401), (
            f"Sin auth esperado 400/401, got {r.status_code}: {r.text[:200]}"
        )

    def test_schema_invalido_400(self):
        """Request con schema invalido debe dar 400."""
        r = requests.get(
            f"{BASE_URL}/memos/received",
            headers={
                "X-User-Email": USER_EMAIL,
                "X-Tenant-Schema": "schema_que_no_existe_para_nada",
            },
            params={"page": 1, "page_size": 10},
        )
        assert r.status_code == 400, f"Esperado 400, got {r.status_code}: {r.text[:200]}"


# ---------------------------------------------------------------------------
# Tests de busqueda de usuarios (selector de destinatarios)
# ---------------------------------------------------------------------------


class TestUserSearch:
    """Tests del endpoint de busqueda de usuarios para el selector de memos."""

    def test_user_search_status_200(self, session):
        """GET /users/search?q=san debe responder 200."""
        r = session.get(f"{BASE_URL}/users/search", params={"q": "san", "limit": 5})
        assert r.status_code == 200, f"Esperado 200, got {r.status_code}: {r.text[:200]}"

    def test_user_search_estructura(self, session):
        """GET /users/search debe tener users[], total_found, search_query."""
        r = session.get(f"{BASE_URL}/users/search", params={"q": "san", "limit": 5})
        assert r.status_code == 200
        data = r.json()
        assert "users" in data, f"Falta 'users' en response: {data}"
        assert isinstance(data["users"], list)
        assert "total_found" in data
        assert "search_query" in data

    def test_user_search_campos_por_usuario(self, session):
        """Cada usuario en /users/search debe tener los campos necesarios para el selector."""
        r = session.get(f"{BASE_URL}/users/search", params={"q": "san", "limit": 5})
        assert r.status_code == 200
        data = r.json()
        if data["users"]:
            user = data["users"][0]
            # Campos necesarios para "Nombre Apellido (Sector)" en el selector
            assert "user_id" in user, f"Falta 'user_id' en usuario: {user}"
            assert "full_name" in user, f"Falta 'full_name' en usuario: {user}"
            assert "email" in user, f"Falta 'email' en usuario: {user}"
            # sector_acronym es necesario para mostrar "(Sector)" en el selector
            assert "sector_acronym" in user, (
                f"Falta 'sector_acronym' en usuario (necesario para selector MEMO): {user}"
            )

    def test_user_search_encuentra_usuario_conocido(self, session):
        """GET /users/search?q=santiago debe encontrar a Santiago Admin."""
        r = session.get(f"{BASE_URL}/users/search", params={"q": "santiago", "limit": 10})
        assert r.status_code == 200
        data = r.json()
        user_ids = [u["user_id"] for u in data["users"]]
        assert USER_ID in user_ids, (
            f"Santiago Admin ({USER_ID}) no aparece en busqueda 'santiago'. "
            f"Encontrados: {user_ids}"
        )


# ---------------------------------------------------------------------------
# Tests de regresion - NOTAS (no debe haberse roto nada)
# ---------------------------------------------------------------------------


class TestNotasRegression:
    """Regresion: NOTAS siguen funcionando igual que antes de MEMOS."""

    def test_notes_received_status_200(self, session):
        """GET /notes/received sigue respondiendo 200."""
        r = session.get(f"{BASE_URL}/notes/received", params={"page": 1, "page_size": 5})
        assert r.status_code == 200, f"REGRESION: /notes/received falla con {r.status_code}: {r.text[:200]}"

    def test_notes_received_estructura(self, session):
        """GET /notes/received sigue teniendo estructura correcta."""
        r = session.get(f"{BASE_URL}/notes/received", params={"page": 1, "page_size": 5})
        assert r.status_code == 200
        data = r.json()
        assert "notes" in data, f"REGRESION: Falta 'notes' en /notes/received: {data}"
        assert "pagination" in data
        assert isinstance(data["notes"], list)

    def test_notes_received_tiene_datos(self, session):
        """GET /notes/received devuelve las notas existentes (hay al menos 1 en 100_test)."""
        r = session.get(f"{BASE_URL}/notes/received", params={"page": 1, "page_size": 5})
        assert r.status_code == 200
        data = r.json()
        assert len(data["notes"]) > 0, (
            "REGRESION: /notes/received no devuelve notas. "
            "Se esperan al menos 4 notas en schema 100_test."
        )

    def test_notes_received_campos_por_nota(self, session):
        """Cada nota en /notes/received tiene los campos esperados."""
        r = session.get(f"{BASE_URL}/notes/received", params={"page": 1, "page_size": 5})
        assert r.status_code == 200
        data = r.json()
        if data["notes"]:
            nota = data["notes"][0]
            assert "document_id" in nota, f"Falta 'document_id' en nota: {nota.keys()}"
            assert "official_number" in nota, f"Falta 'official_number' en nota: {nota.keys()}"
            assert "document_type" in nota, f"Falta 'document_type' en nota: {nota.keys()}"
            # Verificar que las notas siguen siendo tipo NOTA, no MEMO
            assert nota["document_type"] == "NOTA", (
                f"REGRESION: /notes/received devuelve documentos tipo '{nota['document_type']}'"
                f" en vez de 'NOTA'"
            )

    def test_notes_sent_status_200(self, session):
        """GET /notes/sent sigue respondiendo 200."""
        r = session.get(f"{BASE_URL}/notes/sent", params={"page": 1, "page_size": 5})
        assert r.status_code == 200, f"REGRESION: /notes/sent falla con {r.status_code}: {r.text[:200]}"

    def test_notes_sent_tiene_datos(self, session):
        """GET /notes/sent devuelve las notas enviadas existentes."""
        r = session.get(f"{BASE_URL}/notes/sent", params={"page": 1, "page_size": 5})
        assert r.status_code == 200
        data = r.json()
        assert len(data["notes"]) > 0, (
            "REGRESION: /notes/sent no devuelve notas. "
            "Se esperan al menos 1 nota enviada en schema 100_test."
        )

    def test_notes_sent_no_contiene_memos(self, session):
        """GET /notes/sent NO debe devolver documentos de tipo MEMO."""
        r = session.get(f"{BASE_URL}/notes/sent", params={"page": 1, "page_size": 20})
        assert r.status_code == 200
        data = r.json()
        for nota in data["notes"]:
            assert nota["document_type"] != "MEMO", (
                f"REGRESION/CONTAMINACION: /notes/sent devuelve un documento tipo MEMO "
                f"(id: {nota.get('document_id')}). Los MEMOs no deben aparecer en NOTAS."
            )
