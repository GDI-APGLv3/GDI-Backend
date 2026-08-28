from shared.exceptions import (
    BusinessLogicError,
    ConflictError,
    DatabaseBusyError,
    NotFoundError,
    TransientLookupError,
    ValidationError,
    causada_por_pool_saturado,
    exception_to_http_exception,
)
from api_gateway.rest_common import map_exception_to_response


def _envolver(envoltorio, mensaje="algo salio mal"):
    try:
        try:
            raise DatabaseBusyError("Pool de conexiones saturado (acquire timeout)")
        except Exception as e:  # noqa: BLE001 - es el patron que estamos probando
            raise envoltorio(f"{mensaje}: {e}")
    except Exception as envuelta:  # noqa: BLE001
        return envuelta


class TestDeteccionEnLaCadena:
    def test_detecta_el_pool_saturado_envuelto_sin_from(self):
        assert causada_por_pool_saturado(_envolver(BusinessLogicError)) is True

    def test_detecta_el_pool_saturado_envuelto_con_from(self):
        try:
            try:
                raise DatabaseBusyError("pool")
            except DatabaseBusyError as e:
                raise NotFoundError("no encontrado") from e
        except NotFoundError as exc:
            assert causada_por_pool_saturado(exc) is True

    def test_detecta_a_traves_de_varios_niveles(self):
        try:
            try:
                try:
                    raise DatabaseBusyError("pool")
                except Exception as e:
                    raise ValidationError(f"nivel 1: {e}")
            except Exception as e:
                raise BusinessLogicError(f"nivel 2: {e}")
        except BusinessLogicError as exc:
            assert causada_por_pool_saturado(exc) is True

    def test_un_error_normal_no_da_falso_positivo(self):
        try:
            try:
                raise KeyError("campo")
            except Exception as e:
                raise BusinessLogicError(f"algo: {e}")
        except BusinessLogicError as exc:
            assert causada_por_pool_saturado(exc) is False

    def test_una_excepcion_suelta_no_da_falso_positivo(self):
        assert causada_por_pool_saturado(BusinessLogicError("nada que ver")) is False

    def test_no_cuelga_con_una_cadena_circular(self):
        a = BusinessLogicError("a")
        b = BusinessLogicError("b")
        a.__context__ = b
        b.__context__ = a
        assert causada_por_pool_saturado(a) is False


class TestMapeadorDelBackend:

    def test_el_422_enmascarado_ahora_sale_503(self):
        http = exception_to_http_exception(_envolver(
            BusinessLogicError, "Error obteniendo detalle del expediente"))
        assert http.status_code == 503
        assert http.headers.get("Retry-After") == "1"

    def test_el_mensaje_no_filtra_el_detalle_interno(self):
        http = exception_to_http_exception(_envolver(BusinessLogicError))
        assert http.detail["message"] == "Servidor ocupado, reintente en unos segundos"
        assert "acquire timeout" not in str(http.detail)

    def test_el_database_busy_directo_sigue_igual(self):
        http = exception_to_http_exception(DatabaseBusyError("pool"))
        assert http.status_code == 503
        assert http.headers.get("Retry-After") == "1"

    def test_el_transient_lookup_conserva_su_retry_after_de_5(self):
        http = exception_to_http_exception(TransientLookupError("fantasma"))
        assert http.status_code == 503
        assert http.headers.get("Retry-After") == "5"

    def test_un_error_de_negocio_de_verdad_sigue_siendo_422(self):
        http = exception_to_http_exception(BusinessLogicError("el documento ya esta firmado"))
        assert http.status_code == 422

    def test_un_no_encontrado_de_verdad_sigue_siendo_404(self):
        http = exception_to_http_exception(NotFoundError("no existe"))
        assert http.status_code == 404


class TestMapeadorDelGateway:

    def _status(self, exc):
        return map_exception_to_response(exc, "endpoint_de_prueba").status_code

    def test_el_422_enmascarado_ahora_sale_503(self):
        assert self._status(_envolver(BusinessLogicError)) == 503

    def test_el_409_enmascarado_ahora_sale_503(self):
        assert self._status(_envolver(ConflictError)) == 503

    def test_el_404_enmascarado_ahora_sale_503(self):
        assert self._status(_envolver(NotFoundError)) == 503

    def test_el_retry_after_es_1(self):
        r = map_exception_to_response(_envolver(BusinessLogicError), "x")
        assert r.headers.get("Retry-After") == "1"

    def test_el_transient_lookup_conserva_su_retry_after_de_5(self):
        r = map_exception_to_response(TransientLookupError("fantasma"), "x")
        assert r.status_code == 503
        assert r.headers.get("Retry-After") == "5"

    def test_los_errores_de_verdad_no_se_mueven(self):
        assert self._status(BusinessLogicError("ya firmado")) == 422
        assert self._status(NotFoundError("no existe")) == 404
        assert self._status(ConflictError("ya existe")) == 409
        assert self._status(ValidationError("campo invalido")) == 400


class TestAutenticacion:

    def test_auth_rest_deja_pasar_el_database_busy(self):
        import inspect
        import api_gateway.auth_rest as auth_rest

        src = inspect.getsource(auth_rest.validate_rest_api_key)
        assert src.count("except DatabaseBusyError:") == 3, (
            "Faltan re-lanzados de DatabaseBusyError en validate_rest_api_key: "
            "sin ellos un pool saturado vuelve a salir como 401."
        )
        for marcador in ("Error validando schema",
                         "Error validando API Key",
                         "Error validando usuario"):
            i = src.index(marcador)
            previo = src[:i]
            assert previo.rindex("except DatabaseBusyError:") > previo.rindex("try:"), (
                f"El except DatabaseBusyError de '{marcador}' no esta dentro del try correcto."
            )

    def test_los_caminos_publico_y_tad_tambien_dejan_pasar_el_database_busy(self):
        import inspect
        import api_gateway.auth_rest as auth_rest

        for fn in (auth_rest.validate_public_api_key, auth_rest.validate_tad_api_key):
            src = inspect.getsource(fn)
            assert "except DatabaseBusyError:" in src, (
                f"{fn.__name__} sigue convirtiendo el pool saturado en 401."
            )
            assert "status_code=503" in src, (
                f"{fn.__name__} atrapa DatabaseBusyError pero no responde 503."
            )

    def test_publico_y_tad_siguen_siendo_fail_closed(self):
        import inspect
        import api_gateway.auth_rest as auth_rest

        for fn in (auth_rest.validate_public_api_key, auth_rest.validate_tad_api_key):
            src = inspect.getsource(fn)
            i = src.index("except DatabaseBusyError:")
            j = src.index("except Exception", i)
            bloque = src[i:j]
            assert "raise" in bloque, (
                f"{fn.__name__}: el except DatabaseBusyError tiene que LEVANTAR, "
                "no dejar pasar. Fail-closed no se negocia."
            )
            assert "return" not in bloque

    def test_el_decorador_mapea_por_tipo_antes_del_401(self):
        import inspect
        from api_gateway import rest_common

        src = inspect.getsource(rest_common.rest_endpoint)
        i_gdi = src.index("except GDIBaseException as e:")
        i_valida = src.index("ctx = await validate_rest_api_key")
        i_401 = src.index("status_code=401", i_valida)
        assert i_valida < i_gdi < i_401, (
            "El except GDIBaseException tiene que ir despues de validate_rest_api_key "
            "y ANTES del except ValueError que devuelve 401, o el 401 se lo come."
        )
