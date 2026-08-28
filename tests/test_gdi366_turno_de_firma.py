
import pytest

import services.document_service as dsvc
from services.document_service import _ES_ACCIONABLE_SQL, map_display_status


class TestCondicionAccionableUnica:

    def test_la_condicion_mira_el_turno(self):
        assert "es_mi_turno = true" in _ES_ACCIONABLE_SQL, (
            "la condicion accionable tiene que mirar `es_mi_turno`, que es lo que "
            "respeta signing_order. Sin eso el listado vuelve a ofrecer documentos "
            "que el usuario todavia no puede firmar."
        )

    def test_la_condicion_no_usa_la_regla_vieja_del_numerador(self):
        assert "todos_firmantes_comunes_firmaron" not in _ES_ACCIONABLE_SQL, (
            "esa columna solo cubria el caso del numerador y no miraba el orden entre "
            "firmantes comunes. `es_mi_turno` cubre los dos casos."
        )

    def test_exige_ser_firmante_y_no_haber_firmado(self):
        assert "usuario_es_firmante = true" in _ES_ACCIONABLE_SQL
        assert "usuario_ya_firmo = false" in _ES_ACCIONABLE_SQL
        assert "status = 'sent_to_sign'" in _ES_ACCIONABLE_SQL


class TestColumnaEsMiTurnoEnElCTE:

    def _fuente(self):
        import inspect

        return inspect.getsource(dsvc)

    def test_esta_en_las_cuatro_ramas(self):
        src = self._fuente()
        assert src.count("AS es_mi_turno") == 4, (
            f"se esperaban 4 definiciones de es_mi_turno (union_cte y "
            f"union_cte_light, cada uno con la rama de document_draft y la de "
            f"official_documents) y hay {src.count('AS es_mi_turno')}. Si falta en "
            f"una rama, el UNION falla en runtime."
        )

    def test_replica_la_regla_de_bloqueo_de_inicio(self):
        src = self._fuente()
        assert "ds2.status = 'pending'" in src
        assert "ds2.is_numerator = false" in src
        assert "ds2.signing_order < ds.signing_order" in src, (
            "falta la comparacion de orden: es la que hace que un firmante comun "
            "espere a los de orden menor"
        )
        assert "ds.is_numerator = true OR" in src, (
            "falta la rama del numerador: lo bloquea cualquier comun pendiente"
        )


class TestFiltrosDeEstado:

    def _codigo_filtros(self):
        import inspect

        src = inspect.getsource(dsvc.get_user_documents)
        return src

    def test_los_tres_filtros_usan_la_constante(self):
        src = self._codigo_filtros()
        assert src.count("_ES_ACCIONABLE_SQL") >= 3, (
            "'Firmar ahora', 'A mi firma' y 'En proceso de firma' tienen que salir "
            "de la MISMA constante. Si alguno vuelve a tener su copia del predicado, "
            "vuelven a divergir (que es exactamente el bug de GDI-366)."
        )

    def test_a_mi_firma_existe_y_no_filtra_por_policy(self):
        src = self._codigo_filtros()
        assert 'elif status_filter == "A mi firma":' in src, "falta la solapa"

        i = src.index('elif status_filter == "A mi firma":')
        j = src.index('elif status_filter == "En proceso de firma":')
        bloque = "\n".join(
            l for l in src[i:j].splitlines() if not l.strip().startswith("#")
        )
        assert "signature_policy" not in bloque, (
            "la solapa 'A mi firma' muestra TODOS los firmables, con token y sin "
            "token (decision de Santiago 22/08). El filtro de signature_policy es "
            "solo de PortaFirma, que firma en lote."
        )

    def test_firmar_ahora_conserva_el_filtro_de_policy(self):
        src = self._codigo_filtros()
        i = src.index('elif status_filter == "Firmar ahora":')
        j = src.index('elif status_filter == "A mi firma":')
        bloque = src[i:j]
        assert "signature_policy = 'electronic'" in bloque, (
            "GDI-066: PortaFirma firma en LOTE y los documentos con token no se "
            "pueden firmar asi. Peor: useBatchSign.ts no contempla flow='digital' y "
            "los marcaria como 'Firmado' sin que nadie los haya firmado."
        )

    def test_en_proceso_de_firma_es_la_negacion(self):
        src = self._codigo_filtros()
        i = src.index('elif status_filter == "En proceso de firma":')
        bloque = src[i:i + 1200]
        assert "AND NOT (" in bloque and "_ES_ACCIONABLE_SQL" in bloque, (
            "'En proceso de firma' tiene que ser la negacion EXACTA de la condicion "
            "accionable. Si no, los documentos cuyo turno no llego no quedan en "
            "ninguna solapa: desaparecen de la interfaz."
        )


class TestOrderByIndexable:

    def test_no_hay_case_en_el_order_by(self):
        import inspect

        src = inspect.getsource(dsvc.get_user_documents)
        i = src.index("order_by = ")
        bloque = src[i:i + 400]
        assert "CASE" not in bloque, (
            "el ORDER BY volvio a tener un CASE calculado. Ningun indice puede "
            "servir ese orden: Postgres materializa y ordena el universo visible "
            "ENTERO antes del LIMIT (medido: 1 GB de buffers para 20 filas). Lo "
            "accionable va en la solapa 'A mi firma', no arriba del listado."
        )
        assert "last_modified_at DESC" in bloque


class TestBadgeDeLaFila:

    def test_no_es_mi_turno_muestra_en_proceso(self):
        estado = map_display_status(
            status="sent_to_sign",
            rol_usuario="firmante",
            usuario_ya_firmo=False,
            todos_firmantes_comunes_firmaron=False,
            usuario_es_firmante=True,
            document_id=None,
            es_mi_turno=False,
        )
        assert estado == "En proceso de firma", (
            f"un firmante comun cuyo turno todavia no llego NO puede ver 'Firmar "
            f"ahora' (el boton esta deshabilitado). Devolvio {estado!r}."
        )

    def test_es_mi_turno_muestra_firmar_ahora(self):
        estado = map_display_status(
            status="sent_to_sign",
            rol_usuario="firmante",
            usuario_ya_firmo=False,
            todos_firmantes_comunes_firmaron=False,
            usuario_es_firmante=True,
            document_id=None,
            es_mi_turno=True,
        )
        assert estado == "Firmar ahora"

    def test_numerador_en_turno(self):
        estado = map_display_status(
            status="sent_to_sign",
            rol_usuario="numerador",
            usuario_ya_firmo=False,
            todos_firmantes_comunes_firmaron=True,
            usuario_es_firmante=True,
            document_id=None,
            es_mi_turno=True,
        )
        assert estado == "Firmar ahora"

    def test_ya_firmo_no_es_accionable(self):
        estado = map_display_status(
            status="sent_to_sign",
            rol_usuario="firmante",
            usuario_ya_firmo=True,
            todos_firmantes_comunes_firmaron=False,
            usuario_es_firmante=True,
            document_id=None,
            es_mi_turno=False,
        )
        assert estado == "En proceso de firma"

    def test_sin_es_mi_turno_conserva_el_comportamiento_viejo(self):
        estado = map_display_status(
            status="sent_to_sign",
            rol_usuario="firmante",
            usuario_ya_firmo=False,
            todos_firmantes_comunes_firmaron=False,
            usuario_es_firmante=True,
            document_id=None,
        )
        assert estado == "Firmar ahora"

        numerador_esperando = map_display_status(
            status="sent_to_sign",
            rol_usuario="numerador",
            usuario_ya_firmo=False,
            todos_firmantes_comunes_firmaron=False,
            usuario_es_firmante=True,
            document_id=None,
        )
        assert numerador_esperando == "En proceso de firma"
