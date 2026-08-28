import pytest

from shared.numbering import _next_global_sequence


class _ConnFalsa:

    def __init__(self, fetchrow_values):
        self._pendientes = list(fetchrow_values)
        self.queries_fetchrow = []
        self.ejecutados = []

    async def fetchrow(self, query, *args):
        self.queries_fetchrow.append(query)
        return self._pendientes.pop(0)

    async def execute(self, query, *args):
        self.ejecutados.append((query, args))


class TestReciclaAntesDeIncrementar:

    @pytest.mark.asyncio
    async def test_devuelve_el_numero_del_cancelado_mas_viejo(self):
        conn = _ConnFalsa([{"id": "aaaa-1111", "global_sequence": 42}])

        numero = await _next_global_sequence(conn, 2026, caller="test")

        assert numero == 42, "tiene que reusar el número liberado, no emitir uno nuevo"
        assert len(conn.queries_fetchrow) == 1
        assert "COALESCE(MAX(global_sequence)" not in conn.queries_fetchrow[0]

    @pytest.mark.asyncio
    async def test_borra_la_fila_cancelada_y_sus_chunks(self):
        conn = _ConnFalsa([{"id": "aaaa-1111", "global_sequence": 42}])

        await _next_global_sequence(conn, 2026, caller="test")

        borrados = " ".join(q for q, _ in conn.ejecutados)
        assert "DELETE FROM document_chunks" in borrados
        assert "DELETE FROM official_documents" in borrados
        borrado_fila = next(q for q, _ in conn.ejecutados if "official_documents" in q)
        assert "reservation_status = 'CANCELLED'" in borrado_fila

    @pytest.mark.asyncio
    async def test_toma_el_mas_viejo_y_no_pisa_al_de_otra_transaccion(self):
        conn = _ConnFalsa([{"id": "aaaa-1111", "global_sequence": 7}])

        await _next_global_sequence(conn, 2026, caller="test")

        consulta = conn.queries_fetchrow[0]
        assert "ORDER BY reserved_at ASC" in consulta, "FIFO: el más viejo primero"
        assert "FOR UPDATE SKIP LOCKED" in consulta, "otra transacción no puede tomar el mismo"
        assert "numbering_regime = 'GLOBAL'" in consulta, "no mezclar con SPECIAL"


class TestCuandoNoHayNadaQueReciclar:
    @pytest.mark.asyncio
    async def test_incrementa_sobre_el_maximo(self):
        conn = _ConnFalsa([None, {"next_number": 101}])

        numero = await _next_global_sequence(conn, 2026, caller="test")

        assert numero == 101
        assert len(conn.queries_fetchrow) == 2
        assert conn.ejecutados == []

    @pytest.mark.asyncio
    async def test_el_maximo_ignora_los_cancelados(self):
        conn = _ConnFalsa([None, {"next_number": 101}])

        await _next_global_sequence(conn, 2026, caller="test")

        consulta = conn.queries_fetchrow[1]
        assert "reservation_status IN ('RESERVED','CONFIRMING','CONFIRMED')" in consulta
        assert "global_sequence IS NOT NULL" in consulta

    @pytest.mark.asyncio
    async def test_arranca_en_uno_cuando_el_año_esta_vacio(self):
        conn = _ConnFalsa([None, {"next_number": 1}])
        assert await _next_global_sequence(conn, 2026, caller="test") == 1


class TestLasCuatroPuertasCompartenLaRegla:

    @pytest.mark.asyncio
    async def test_el_reciclado_no_depende_de_quien_llame(self):
        resultados = []
        for quien in (
            "reserve_number GLOBAL",
            "reserve_citizen_number",
            "generate_official_number",
            "generate_citizen_official_number",
        ):
            conn = _ConnFalsa([{"id": "aaaa-1111", "global_sequence": 55}])
            resultados.append(await _next_global_sequence(conn, 2026, caller=quien))

        assert resultados == [55, 55, 55, 55]
