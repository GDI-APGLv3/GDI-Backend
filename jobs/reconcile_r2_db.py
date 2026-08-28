
import json
import os
from datetime import datetime, timedelta, timezone

from shared.logging import get_logger

log = get_logger(__name__)


RECONCILE_HOUR   = int(os.getenv("RECONCILE_HOUR", "2"))
RECONCILE_MINUTE = int(os.getenv("RECONCILE_MINUTE", "45"))

RECONCILE_GRACE_HOURS = int(os.getenv("RECONCILE_GRACE_HOURS", "6"))

RECONCILE_MAX_KEYS = int(os.getenv("RECONCILE_MAX_KEYS", "200000"))

RECONCILE_MAX_DETALLE = int(os.getenv("RECONCILE_MAX_DETALLE", "20"))

RECONCILE_ONBOARDING = os.getenv("RECONCILE_ONBOARDING", "").strip().lower() in (
    "1", "true", "yes", "si", "sí",
)

RECONCILE_ALERTA_MASIVA = int(os.getenv("RECONCILE_ALERTA_MASIVA", "500"))


async def _numeros_en_r2(schema_name: str) -> tuple[dict[str, dict], set[str], bool]:
    from services.r2_client import r2_list

    numeros: dict[str, dict] = {}
    duplicados: set[str] = set()
    ajenos = 0
    truncado = False

    for bucket in ("oficial", "preoficial"):
        try:
            objetos, trunc_bucket = await r2_list(
                schema_name=schema_name, bucket=bucket, max_keys=RECONCILE_MAX_KEYS
            )
        except ValueError:
            log.info(
                "reconcile.sin_bucket schema=%s bucket=%s — se omite del inventario",
                schema_name, bucket,
            )
            continue

        truncado = truncado or trunc_bucket
        for obj in objetos:
            key = obj["key"]
            if not key.lower().endswith(".pdf"):
                ajenos += 1
                continue
            entrada = dict(obj)
            entrada["location"] = bucket
            num = key[:-4]
            if num in numeros and numeros[num].get("location") == "oficial":
                duplicados.add(num)
                continue
            numeros[num] = entrada

    if ajenos:
        log.info(
            "reconcile.objetos_ajenos schema=%s count=%d — no terminan en .pdf, ignorados",
            schema_name, ajenos,
        )
    return numeros, duplicados, truncado


async def _numeros_en_bd(schema_name: str) -> tuple[dict[str, dict], dict[str, dict]]:
    from database import fetch_all

    filas = await fetch_all(
        """
        SELECT official_number,
               id::text            AS doc_id,
               reservation_status,
               signed_at,
               updated_at,
               created_at,
               pdf_location
        FROM official_documents
        WHERE official_number IS NOT NULL
        """,
        schema_name=schema_name,
    )

    oficiales: dict[str, dict] = {}
    en_transito: dict[str, dict] = {}

    for f in filas:
        num = f["official_number"]
        es_oficial = (
            f["signed_at"] is not None
            or f["reservation_status"] == "CONFIRMED"
        )
        destino = oficiales if es_oficial else en_transito
        if num not in destino or es_oficial:
            destino[num] = dict(f)

    return oficiales, en_transito


def _aware(momento):
    if momento is None:
        return None
    if momento.tzinfo is None:
        return momento.replace(tzinfo=timezone.utc)
    return momento


def _mas_reciente(*momentos):
    validos = [m for m in (_aware(x) for x in momentos) if m is not None]
    return max(validos) if validos else None


def _es_reciente(momento, corte: datetime) -> bool:
    momento = _aware(momento)
    if momento is None:
        return False
    return momento > corte


async def _conciliar_tenant(schema_name: str, corte: datetime) -> dict:
    en_r2, duplicados, truncado = await _numeros_en_r2(schema_name)
    oficiales, en_transito = await _numeros_en_bd(schema_name)

    hallazgos: list[dict] = []

    for numero, obj in en_r2.items():
        if numero in oficiales:
            continue
        if _es_reciente(obj.get("last_modified"), corte):
            continue

        fila = en_transito.get(numero)
        hallazgos.append({
            "kind": "pdf_sin_documento",
            "official_number": numero,
            "document_id": fila["doc_id"] if fila else None,
            "detail": {
                "estado_en_bd": (
                    fila["reservation_status"] if fila else "SIN FILA EN LA BASE"
                ),
                "r2_size": obj.get("size"),
                "r2_last_modified": (
                    obj["last_modified"].isoformat()
                    if obj.get("last_modified") else None
                ),
            },
        })

    if truncado:
        log.warning(
            "reconcile.direccion_b_omitida schema=%s — listado de R2 truncado en %d objetos",
            schema_name, RECONCILE_MAX_KEYS,
        )
    else:
        for numero, fila in oficiales.items():
            if numero in en_r2:
                continue
            referencia = _mas_reciente(
                fila.get("signed_at"), fila.get("updated_at"), fila.get("created_at")
            )
            if _es_reciente(referencia, corte):
                continue

            hallazgos.append({
                "kind": "documento_sin_pdf",
                "official_number": numero,
                "document_id": fila["doc_id"],
                "detail": {
                    "estado_en_bd": fila["reservation_status"],
                    "pdf_location_en_bd": fila.get("pdf_location"),
                    "signed_at": (
                        fila["signed_at"].isoformat() if fila.get("signed_at") else None
                    ),
                },
            })

    for numero, fila in oficiales.items():
        obj = en_r2.get(numero)
        if not obj:
            continue
        loc_r2 = obj.get("location", "oficial")
        loc_bd = fila.get("pdf_location") or "oficial"
        if loc_r2 == loc_bd:
            continue
        if _es_reciente(obj.get("last_modified"), corte):
            continue
        hallazgos.append({
            "kind": "pdf_location_desincronizada",
            "official_number": numero,
            "document_id": fila["doc_id"],
            "detail": {
                "pdf_location_en_bd": loc_bd,
                "bucket_real_en_r2": loc_r2,
                "reparacion": (
                    f"UPDATE official_documents SET pdf_location = '{loc_r2}' "
                    f"WHERE id = '{fila['doc_id']}'"
                ),
            },
        })

    _KIND_PDF_DUPLICADO = "pdf_duplicado_en_ambos_buckets"
    if duplicados and os.getenv("RECONCILE_EMITIR_DUPLICADOS", "1").strip().lower() in (
        "1", "true", "yes",
    ):
        for numero in duplicados:
            obj = en_r2.get(numero)
            if obj and _es_reciente(obj.get("last_modified"), corte):
                continue
            fila = oficiales.get(numero) or en_transito.get(numero)
            hallazgos.append({
                "kind": _KIND_PDF_DUPLICADO,
                "official_number": numero,
                "document_id": fila["doc_id"] if fila else None,
                "detail": {
                    "estado_en_bd": fila["reservation_status"] if fila else "SIN FILA EN LA BASE",
                    "explicacion": (
                        "El PDF está en oficial/ Y en preoficial/ a la vez: "
                        "basura del cron de sellado a medio camino (copió a "
                        "oficial pero no borró la fuente). No es pérdida de "
                        "datos, pero preoficial/ debería quedar vacío."
                    ),
                },
            })
    elif duplicados:
        log.warning(
            "reconcile.duplicados_sin_reportar schema=%s count=%d numeros=%s — "
            "requiere migración del CHECK reconciliation_findings_kind_chk "
            "para poder loguearlos como hallazgo (kind=%s, flag "
            "RECONCILE_EMITIR_DUPLICADOS)",
            schema_name, len(duplicados), sorted(duplicados)[:20], _KIND_PDF_DUPLICADO,
        )

    return {
        "schema_name": schema_name,
        "hallazgos": hallazgos,
        "objetos_r2": len(en_r2),
        "oficiales_bd": len(oficiales),
        "truncado": truncado,
    }


async def _persistir(schema_name: str, hallazgos: list[dict]) -> list[dict]:
    from database import fetch_all, execute

    nuevos: list[dict] = []
    fallidos = 0

    for h in hallazgos:
        try:
            filas = await fetch_all(
                """
                INSERT INTO public.reconciliation_findings
                    (schema_name, kind, official_number, document_id, detail)
                VALUES ($1, $2, $3, $4::uuid, $5::jsonb)
                ON CONFLICT (schema_name, kind, official_number) DO UPDATE
                   SET last_seen_at = NOW(),
                       detail       = EXCLUDED.detail,
                       -- Si había sido marcado como resuelto y volvió a aparecer,
                       -- se reabre y se vuelve a alertar: es un caso distinto.
                       resolved_at  = NULL,
                       alerted_at   = CASE
                           WHEN public.reconciliation_findings.resolved_at IS NOT NULL
                           THEN NULL
                           ELSE public.reconciliation_findings.alerted_at
                       END
                RETURNING id::text, alerted_at
                """,
                schema_name,
                h["kind"],
                h["official_number"],
                h["document_id"],
                h["detail"],
                schema_name="public",
            )
        except Exception:
            fallidos += 1
            log.error(
                "reconcile.persistir_hallazgo_fallo schema=%s kind=%s numero=%s",
                schema_name, h.get("kind"), h.get("official_number"),
                exc_info=True,
            )
            continue

        if filas and filas[0]["alerted_at"] is None:
            nuevos.append({**h, "id": filas[0]["id"]})

    if fallidos:
        log.error(
            "reconcile.persistir_hallazgos_fallidos schema=%s fallidos=%d total=%d",
            schema_name, fallidos, len(hallazgos),
        )

    if nuevos:
        await execute(
            """
            UPDATE public.reconciliation_findings
            SET alerted_at = NOW()
            WHERE id = ANY($1::uuid[])
            """,
            [n["id"] for n in nuevos],
            schema_name="public",
        )

    return nuevos


async def _marcar_resueltos(schema_name: str, hallazgos: list[dict]) -> int:
    from database import execute

    vigentes = [f"{h['kind']}|{h['official_number']}" for h in hallazgos]
    resultado = await execute(
        """
        UPDATE public.reconciliation_findings
        SET resolved_at = NOW()
        WHERE schema_name = $1
          AND resolved_at IS NULL
          AND (kind || '|' || official_number) <> ALL($2::text[])
        """,
        schema_name,
        vigentes,
        schema_name="public",
    )
    try:
        return int(str(resultado).split()[-1])
    except (ValueError, IndexError):
        return 0


async def _run_reconcile() -> None:
    from shared.alerts import send_alert_mail
    from shared.tenant_validation import get_valid_schemas

    inicio = datetime.now(timezone.utc)
    corte = inicio - timedelta(hours=RECONCILE_GRACE_HOURS)
    log.info(
        "reconcile.run_start ts=%s grace=%dh",
        inicio.strftime("%Y-%m-%d %H:%M UTC"), RECONCILE_GRACE_HOURS,
    )

    all_schemas = await get_valid_schemas()
    tenants = [s for s in all_schemas if s not in ("public", "100_test")]

    if not tenants and "100_test" in all_schemas:
        tenants = ["100_test"]
        log.info("reconcile.usando_100_test — es el único tenant de este ambiente")

    if not tenants:
        log.error(
            "reconcile.sin_tenants — no hay ningún tenant que revisar "
            "(schemas visibles: %s). El conciliador no revisó NADA.",
            all_schemas,
        )
        return

    lineas: list[str] = []
    detalle: list[str] = []
    total_nuevos = 0
    total_abiertos = 0
    total_resueltos = 0
    tenants_con_error: list[str] = []
    tenants_parciales: list[str] = []

    for schema_name in tenants:
        try:
            res = await _conciliar_tenant(schema_name, corte)
        except Exception:
            log.exception("reconcile.tenant_error schema=%s", schema_name)
            tenants_con_error.append(schema_name)
            continue

        hallazgos = res["hallazgos"]
        if res["truncado"]:
            tenants_parciales.append(schema_name)

        try:
            nuevos = await _persistir(schema_name, hallazgos)
            resueltos = await _marcar_resueltos(schema_name, hallazgos)
        except Exception:
            log.exception("reconcile.persist_error schema=%s", schema_name)
            tenants_con_error.append(schema_name)
            continue

        total_nuevos += len(nuevos)
        total_abiertos += len(hallazgos)
        total_resueltos += resueltos

        if hallazgos or resueltos:
            lineas.append(
                f"  {schema_name}: {len(hallazgos)} abiertos "
                f"({len(nuevos)} nuevos) | {resueltos} resueltos "
                f"| R2 {res['objetos_r2']} objetos / BD {res['oficiales_bd']} oficiales"
                + ("  ⚠ LISTADO PARCIAL" if res["truncado"] else "")
            )

        for h in nuevos[:RECONCILE_MAX_DETALLE]:
            que = (
                "PDF en R2 sin documento oficial"
                if h["kind"] == "pdf_sin_documento"
                else "documento oficial sin PDF en R2"
            )
            detalle.append(
                f"  [{schema_name}] {h['official_number']} — {que} "
                f"({h['detail'].get('estado_en_bd')})"
            )

    duracion = (datetime.now(timezone.utc) - inicio).total_seconds()
    log.info(
        "reconcile.run_end nuevos=%d abiertos=%d resueltos=%d errores=%d dur=%.1fs",
        total_nuevos, total_abiertos, total_resueltos,
        len(tenants_con_error), duracion,
    )

    if not (total_nuevos or tenants_con_error or tenants_parciales):
        return

    if RECONCILE_ONBOARDING:
        log.warning(
            "reconcile.onboarding — %d hallazgo(s) grabados SIN mandar mail. "
            "Revisar con: SELECT kind, count(*) FROM public.reconciliation_findings "
            "WHERE resolved_at IS NULL GROUP BY 1; y apagar RECONCILE_ONBOARDING.",
            total_nuevos,
        )
        return

    if total_nuevos:
        subject = f"[GDI CONCILIADOR] {total_nuevos} inconsistencia(s) nueva(s) R2↔BD"
    else:
        subject = "[GDI CONCILIADOR] la corrida no pudo completarse"

    cuerpo = [
        f"Corrida: {inicio.strftime('%Y-%m-%d %H:%M UTC')} ({duracion:.0f}s)",
        f"Ventana de gracia: {RECONCILE_GRACE_HOURS} h "
        f"(no se reporta nada tocado después de {corte.strftime('%H:%M UTC')}).",
        "",
        f"Hallazgos nuevos: {total_nuevos}",
        f"Total abiertos:   {total_abiertos}",
        f"Cerrados en esta corrida: {total_resueltos}",
    ]

    if total_nuevos >= RECONCILE_ALERTA_MASIVA:
        cuerpo += [
            "",
            f"⚠ {total_nuevos} hallazgos nuevos de una sola vez: es MUY probable que",
            "  sea deuda histórica que aparece por primera vez (o un import masivo en",
            "  curso), no algo que se rompió anoche. Antes de tratarlo como incidente,",
            "  mirar la distribución por tenant y por fecha en reconciliation_findings.",
            "  Si es la primera corrida del job en este ambiente, conviene repetirla",
            "  con RECONCILE_ONBOARDING=1 para tomar la foto sin mail.",
        ]

    if lineas:
        cuerpo += ["", "Por tenant:"] + lineas

    if detalle:
        cuerpo += ["", "Detalle de los nuevos:"] + detalle
        omitidos = total_nuevos - len(detalle)
        if omitidos > 0:
            cuerpo.append(f"  ... y {omitidos} más (ver reconciliation_findings).")

    if tenants_parciales:
        cuerpo += [
            "",
            f"⚠ Listado de R2 TRUNCADO en {', '.join(tenants_parciales)} "
            f"(tope {RECONCILE_MAX_KEYS}).",
            "  En esos tenants NO se buscaron documentos sin PDF: con el listado",
            "  incompleto no se puede afirmar que un PDF falte. Subir RECONCILE_MAX_KEYS.",
        ]

    if tenants_con_error:
        cuerpo += [
            "",
            f"⚠ {len(tenants_con_error)} tenant(s) no pudieron conciliarse: "
            f"{', '.join(tenants_con_error)}. Revisar logs del backend.",
        ]

    cuerpo += [
        "",
        "El conciliador NO corrige nada: solo detecta. La resolución es manual.",
        "Los hallazgos viven en public.reconciliation_findings (se alerta una sola",
        "vez por hallazgo; mientras siga abierto se actualiza last_seen_at).",
    ]

    await send_alert_mail(
        subject=subject,
        body="\n".join(cuerpo),
        schema_name=None,
    )


async def run_reconcile_r2_db() -> None:
    from shared.advisory_lock import global_job_lock, LOCK_ID_RECONCILE_R2_DB

    try:
        async with global_job_lock(
            LOCK_ID_RECONCILE_R2_DB, "reconcile_r2_db"
        ) as got_lock:
            if not got_lock:
                return
            await _run_reconcile()
    except Exception:
        log.exception("reconcile.fatal_error")


def schedule_reconcile_r2_db(scheduler) -> None:
    scheduler.add_job(
        run_reconcile_r2_db,
        "cron",
        hour=RECONCILE_HOUR,
        minute=RECONCILE_MINUTE,
        timezone="UTC",
        id="reconcile_r2_db",
        max_instances=1,
        coalesce=True,
    )
    log.info(
        "reconcile_r2_db.scheduled cron=%d:%02d UTC grace=%dh",
        RECONCILE_HOUR, RECONCILE_MINUTE, RECONCILE_GRACE_HOURS,
    )
