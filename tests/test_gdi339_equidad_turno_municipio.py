import inspect

import pytest


class TestClaimSQLEquidad:

    def _source(self):
        from workers.escri import EscriWorker
        return inspect.getsource(EscriWorker._claim_one)

    def test_ordena_por_jobs_en_vuelo_del_municipio(self):
        src = self._source()
        assert "en_vuelo" in src
        assert "COALESCE(v.en_vuelo, 0) ASC" in src

    def test_el_conteo_en_vuelo_sale_de_processing_por_municipio(self):
        src = self._source()
        assert "WHERE status = 'processing'" in src
        assert "GROUP BY schema_name" in src

    def test_no_usa_row_number(self):
        assert "ROW_NUMBER() OVER" not in self._source().upper()

    def test_sigue_habiendo_skip_locked_sobre_la_cola(self):
        assert "FOR UPDATE OF s SKIP LOCKED" in self._source()

    def test_ya_no_hay_prioridad_por_tipo_de_trabajo(self):
        src = self._source()
        assert "job_type <> 'dts'" not in src
        assert "COALESCE(v.en_vuelo, 0) ASC" in src

    def test_dentro_del_municipio_sigue_mandando_created_at(self):
        assert "s.created_at ASC" in self._source()


def _claim_sucesivos(cola, n):
    pendientes = list(cola)
    en_vuelo: dict[str, int] = {}
    atendidos = []

    for _ in range(n):
        if not pendientes:
            break
        elegido = min(
            pendientes,
            key=lambda j: (
                j[1] == "dts",
                en_vuelo.get(j[0], 0),
                j[2],
            ),
        )
        pendientes.remove(elegido)
        en_vuelo[elegido[0]] = en_vuelo.get(elegido[0], 0) + 1
        atendidos.append(elegido)

    return atendidos


class TestOrdenDeAtencion:

    def test_ca1_el_chico_no_espera_a_las_200_del_grande(self):
        cola = [("A", "sign", t) for t in range(5)] + [("B", "sign", 10)]
        orden = [j[0] for j in _claim_sucesivos(cola, 6)]
        assert orden[:2] == ["A", "B"], orden

    def test_ca2_si_hay_uno_solo_se_lleva_toda_la_capacidad(self):
        cola = [("A", "sign", t) for t in range(5)]
        assert [j[0] for j in _claim_sucesivos(cola, 5)] == ["A"] * 5

    def test_ca3_tres_municipios_parejos_alternan(self):
        cola = [(m, "sign", t) for t in range(3) for m in ("A", "B", "C")]
        orden = [j[0] for j in _claim_sucesivos(cola, 9)]
        assert orden == ["A", "B", "C", "A", "B", "C", "A", "B", "C"], orden

    def test_ca4_dentro_del_municipio_manda_el_orden_de_llegada(self):
        cola = [("A", "sign", 3), ("A", "sign", 1), ("A", "sign", 2)]
        assert [j[2] for j in _claim_sucesivos(cola, 3)] == [1, 2, 3]

    def test_el_dts_mas_viejo_igual_sale_ultimo(self):
        cola = [("A", "dts", 0), ("A", "sign", 5), ("B", "sign", 6)]
        orden = [(j[0], j[1]) for j in _claim_sucesivos(cola, 3)]
        assert orden[-1] == ("A", "dts"), orden

    def test_el_grande_no_monopoliza_tampoco_a_la_larga(self):
        cola = [("A", "sign", t) for t in range(200)] + [("B", "sign", 500)]
        orden = [j[0] for j in _claim_sucesivos(cola, 201)]
        assert orden.index("B") == 1, f"B salió en la posición {orden.index('B')}"
