
from typing import List, Dict, Any, Optional
from database import get_conn
from datetime import datetime
from shared.logging import get_logger

logger = get_logger(__name__)

_ES_ACCIONABLE_SQL = """status = 'sent_to_sign'
            AND usuario_es_firmante = true
            AND usuario_ya_firmo = false
            AND es_mi_turno = true"""

_STATUS_FILTERS_QUE_CUENTAN_UNIVERSO = frozenset({"A mi firma"})


def map_display_status(status: str, rol_usuario: str, usuario_ya_firmo: bool,
                       todos_firmantes_comunes_firmaron: bool, usuario_es_firmante: bool = False, document_id: str = None,
                       es_mi_turno: Optional[bool] = None) -> str:

    if status == "signed":
        state_code = "SIGNED"

    elif status == "rejected":
        state_code = "EDITING"

    elif status == "draft":
        state_code = "EDITING"

    elif status == "sent_to_sign":
        if usuario_es_firmante and not usuario_ya_firmo:
            if es_mi_turno is not None:
                state_code = "SIGN_NOW" if es_mi_turno else "SIGNING_PROCESS"
            elif rol_usuario == "numerador":
                state_code = "SIGN_NOW" if todos_firmantes_comunes_firmaron else "SIGNING_PROCESS"
            else:
                state_code = "SIGN_NOW"
        else:
            state_code = "SIGNING_PROCESS"

    else:
        state_code = "SIGNING_PROCESS"

    if state_code == "SIGN_NOW":
        return "Firmar ahora"
    elif state_code == "SIGNING_PROCESS":
        return "En proceso de firma"
    elif state_code == "SIGNED":
        return "Firmado"
    elif state_code == "EDITING":
        return "En edición"
    else:
        return "Estado desconocido"

async def get_user_documents(
    user_id: str,
    status_filter: Optional[str] = None,
    date_filter: Optional[str] = None,
    date_from_str: Optional[str] = None,
    date_to_str: Optional[str] = None,
    document_type: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
    doc_number: Optional[str] = None,
    search: Optional[str] = None,
    min_signers: Optional[int] = None,
    sector_filter: Optional[str] = None,
    case_id: Optional[str] = None,
    exclude_reserved: bool = False,
    *,
    schema_name: str,
    signature_mode: str = "electronic",
    _force_two_acquires: bool = False,
) -> Dict[str, Any]:
    page_size = min(max(page_size, 1), 100)
    offset = (page - 1) * page_size

    user_sectors_list = None
    if _force_two_acquires:
        from services.case_service import CaseService
        user_viewable_sectors = await CaseService.get_user_viewable_sector_ids(user_id, schema_name=schema_name)
        user_sectors_list = user_viewable_sectors if user_viewable_sectors else []

    union_cte = """
    SELECT
        d.id AS document_id,
        d.reference,
        d.status::text AS status,
        d.last_modified_at,
        COALESCE(u.full_name, creator_citizen.full_name)::text AS last_editor_full_name,
        u.profile_picture_url AS last_editor_profile_picture_url,
        creator_citizen.id AS last_editor_citizen_id,
        creator_citizen.country_id AS last_editor_citizen_country_id,
        dt.acronym::text AS acronym,
        dt.signature_policy::text AS signature_policy,
        dt.is_reserved AS document_type_is_reserved,
        dt.visibility::text AS document_type_visibility,
        NULL::text AS official_number,
        d.short_resume,
        d.resume,
        NULL::jsonb AS linked_cases,
        NULL::jsonb AS linked_records,
        creator_s.acronym AS creator_sector_acronym,
        creator_d.acronym AS creator_dept_acronym,
        u_sender.full_name AS sent_by_name,
        COALESCE(d.content->>'html', '') AS content_html,
        -- BACKEND-05: signers_names, signers_count y signers calculados UNA SOLA VEZ
        -- via LATERAL JOIN (antes eran 3 subqueries separadas sobre document_signers).
        COALESCE(signer_agg.signers_names, '') AS signers_names,
        COALESCE(signer_agg.signers_count, 0) AS signers_count,
        signer_agg.signers AS signers,
        CASE
          WHEN ds.is_numerator = true THEN 'numerador'
          WHEN d.created_by = $1::uuid THEN 'creador'
          WHEN ds.is_numerator = false AND ds.user_id IS NOT NULL THEN 'firmante'
          WHEN EXISTS (SELECT 1 FROM users u2 WHERE u2.id = d.created_by AND u2.sector_id = ANY($2::uuid[])) THEN 'sector'
          ELSE 'otro'
        END AS rol_usuario,
        ds.signed_at IS NOT NULL AS usuario_ya_firmo,
        ds.user_id IS NOT NULL AS usuario_es_firmante,
        -- GDI-366: "es mi turno de firmar". Replica `_is_my_turn_condition`
        -- (services/documents/retrieval/pending_signatures.py:44-56), que es lo que
        -- YA usa la pantalla de INICIO y lo mismo que decide si el boton Firmar se
        -- habilita (`_can_user_sign`, details_builder.py:541-565).
        -- El NOT EXISTS busca firmantes que ME BLOQUEAN:
        --   * si soy numerador -> me bloquea CUALQUIER firmante comun pendiente
        --     (el numerador firma al final);
        --   * si soy comun -> solo me bloquean los comunes de signing_order MENOR;
        --     el numerador nunca bloquea a un comun.
        -- Antes de GDI-366 el listado ignoraba `signing_order`, asi que ponia
        -- "Firmar ahora" arriba de todo en documentos cuyo boton estaba
        -- deshabilitado. Dos pantallas decian cosas distintas del mismo documento.
        CASE WHEN ds.user_id IS NULL THEN false ELSE NOT EXISTS (
          SELECT 1 FROM document_signers ds2
          WHERE ds2.document_id = d.id
            AND ds2.status = 'pending'
            AND ds2.is_numerator = false
            AND (ds.is_numerator = true OR ds2.signing_order < ds.signing_order)
        ) END AS es_mi_turno,
        COALESCE(
          (SELECT bool_and(s2.signed_at IS NOT NULL)
           FROM document_signers s2
           WHERE s2.document_id = d.id AND (s2.is_numerator = false OR s2.is_numerator IS NULL)),
          true
        ) AS todos_firmantes_comunes_firmaron
    FROM document_draft d
    JOIN document_types dt ON d.document_type_id = dt.id
    LEFT JOIN users u ON d.created_by = u.id
    -- GDI-130 (TAD): documentos creados por un ciudadano tienen created_by NULL
    -- y created_by_citizen seteado (migracion 087). Sin este LEFT JOIN,
    -- last_editor_full_name quedaba NULL y rompia el front del buscador global.
    LEFT JOIN citizens creator_citizen ON d.created_by_citizen = creator_citizen.id
    LEFT JOIN sectors creator_s ON u.sector_id = creator_s.id
    LEFT JOIN departments creator_d ON creator_s.department_id = creator_d.id
    LEFT JOIN users u_sender ON d.sent_by = u_sender.id
    LEFT JOIN document_signers ds ON ds.document_id = d.id AND ds.user_id = $1::uuid
    -- BACKEND-05: calcular todos los agregados de firmantes una sola vez
    LEFT JOIN LATERAL (
        SELECT
            string_agg(COALESCE(signer_u.full_name, signer_c.full_name), ' ' ORDER BY signer_ds.signing_order NULLS LAST, signer_ds.user_id, signer_ds.citizen_id) AS signers_names,
            COUNT(*) AS signers_count,
            jsonb_agg(jsonb_build_object(
                'user_id', signer_ds.user_id,
                'citizen_id', signer_ds.citizen_id,
                'full_name', COALESCE(signer_u.full_name, signer_c.full_name),
                'profile_picture_url', signer_u.profile_picture_url,
                'signed', signer_ds.signed_at IS NOT NULL,
                'is_numerator', signer_ds.is_numerator
            ) ORDER BY signer_ds.signing_order NULLS LAST, signer_ds.user_id, signer_ds.citizen_id) AS signers
        FROM document_signers signer_ds
        -- GDI-130 (TAD): firmantes ciudadano tienen user_id NULL y citizen_id
        -- seteado -- antes el INNER JOIN a users los excluia del agregado
        -- (signers_names/signers_count/signers quedaban sin ese firmante).
        LEFT JOIN users signer_u ON signer_u.id = signer_ds.user_id
        LEFT JOIN citizens signer_c ON signer_c.id = signer_ds.citizen_id
        WHERE signer_ds.document_id = d.id
    ) signer_agg ON true
    WHERE dt.is_active = true
      AND d.is_deleted = false
      AND NOT EXISTS (SELECT 1 FROM official_documents WHERE id = d.id AND signed_at IS NOT NULL)
      AND (
        d.created_by = $1::uuid
        OR (EXISTS (
            SELECT 1 FROM document_signers s
            WHERE s.document_id = d.id
              AND s.user_id = $1::uuid
              AND d.created_by != $1::uuid
              AND d.status IN ('sent_to_sign', 'signed')
        ))
        OR (
            EXISTS (SELECT 1 FROM users u2 WHERE u2.id = d.created_by AND u2.sector_id = ANY($2::uuid[]))
            AND d.created_by != $1::uuid
            AND d.status IN ('sent_to_sign', 'signed')
            AND dt.acronym NOT IN ('NOTA', 'MEMO')
        )
      )

    UNION ALL

    SELECT
        o.id AS document_id,
        o.reference,
        'signed'::text AS status,
        o.signed_at AS last_modified_at,
        COALESCE(u.full_name, numerator_citizen.full_name)::text AS last_editor_full_name,
        u.profile_picture_url AS last_editor_profile_picture_url,
        numerator_citizen.id AS last_editor_citizen_id,
        numerator_citizen.country_id AS last_editor_citizen_country_id,
        dt.acronym::text AS acronym,
        dt.signature_policy::text AS signature_policy,
        dt.is_reserved AS document_type_is_reserved,
        dt.visibility::text AS document_type_visibility,
        o.official_number::text AS official_number,
        o.short_resume,
        o.resume,
        (SELECT jsonb_agg(jsonb_build_object('case_id', c.id, 'case_number', c.case_number))
         FROM case_official_documents cod
         JOIN cases c ON c.id = cod.case_id
         WHERE cod.official_document_id = o.id AND cod.is_active = true) AS linked_cases,
        (SELECT jsonb_agg(jsonb_build_object('record_id', r.id, 'record_number', r.record_number))
         FROM record_document_links rdl
         JOIN records r ON r.id = rdl.record_id
         WHERE rdl.document_id = o.id) AS linked_records,
        creator_s.acronym AS creator_sector_acronym,
        creator_d.acronym AS creator_dept_acronym,
        u_sender.full_name AS sent_by_name,
        COALESCE(o.content->>'html', '') AS content_html,
        -- BACKEND-05: signers_names, signers_count y signers calculados UNA SOLA VEZ
        -- via LATERAL JOIN (antes eran 3 subqueries separadas sobre document_signers).
        COALESCE(signer_agg.signers_names, '') AS signers_names,
        COALESCE(signer_agg.signers_count, 0) AS signers_count,
        signer_agg.signers AS signers,
        CASE
          WHEN ds.is_numerator THEN 'numerador'
          WHEN ds.user_id IS NOT NULL THEN 'firmante'
          WHEN d.created_by = $1::uuid THEN 'creador'
          WHEN o.signer_sector_ids && $2::uuid[] THEN 'sector'
          ELSE 'otro'
        END AS rol_usuario,
        ds.signed_at IS NOT NULL AS usuario_ya_firmo,
        ds.user_id IS NOT NULL AS usuario_es_firmante,
        -- GDI-366: la columna tiene que existir en las DOS ramas del UNION. En un
        -- documento oficial ya firmaron todos, asi que el turno no aplica nunca.
        false AS es_mi_turno,
        (SELECT bool_and(s2.signed_at IS NOT NULL)
         FROM document_signers s2
         WHERE s2.document_id = o.id AND (s2.is_numerator = false OR s2.is_numerator IS NULL)
        ) AS todos_firmantes_comunes_firmaron
    FROM official_documents o
    JOIN document_draft d ON o.id = d.id
    JOIN document_types dt ON o.document_type_id = dt.id
    LEFT JOIN users u ON o.numerator_id = u.id
    -- GDI-130 (TAD): documentos autofirmados/autonumerados por un ciudadano
    -- tienen numerator_id NULL y numerator_citizen seteado (migracion 087,
    -- parche 23/07). Sin este LEFT JOIN, last_editor_full_name quedaba NULL
    -- y rompia el front del buscador global.
    LEFT JOIN citizens numerator_citizen ON o.numerator_citizen = numerator_citizen.id
    LEFT JOIN users creator_u ON d.created_by = creator_u.id
    LEFT JOIN sectors creator_s ON creator_u.sector_id = creator_s.id
    LEFT JOIN departments creator_d ON creator_s.department_id = creator_d.id
    LEFT JOIN users u_sender ON d.sent_by = u_sender.id
    LEFT JOIN document_signers ds ON ds.document_id = o.id AND ds.user_id = $1::uuid
    -- BACKEND-05: calcular todos los agregados de firmantes una sola vez
    LEFT JOIN LATERAL (
        SELECT
            string_agg(COALESCE(signer_u.full_name, signer_c.full_name), ' ' ORDER BY signer_ds.signing_order NULLS LAST, signer_ds.user_id, signer_ds.citizen_id) AS signers_names,
            COUNT(*) AS signers_count,
            jsonb_agg(jsonb_build_object(
                'user_id', signer_ds.user_id,
                'citizen_id', signer_ds.citizen_id,
                'full_name', COALESCE(signer_u.full_name, signer_c.full_name),
                'profile_picture_url', signer_u.profile_picture_url,
                'signed', signer_ds.signed_at IS NOT NULL,
                'is_numerator', signer_ds.is_numerator
            ) ORDER BY signer_ds.signing_order NULLS LAST, signer_ds.user_id, signer_ds.citizen_id) AS signers
        FROM document_signers signer_ds
        -- GDI-130 (TAD): firmantes ciudadano tienen user_id NULL y citizen_id
        -- seteado -- antes el INNER JOIN a users los excluia del agregado.
        LEFT JOIN users signer_u ON signer_u.id = signer_ds.user_id
        LEFT JOIN citizens signer_c ON signer_c.id = signer_ds.citizen_id
        WHERE signer_ds.document_id = o.id
    ) signer_agg ON true
    WHERE dt.is_active = true
      AND d.is_deleted = false
      AND o.signed_at IS NOT NULL
      AND (
        d.created_by = $1::uuid
        OR (EXISTS (
            SELECT 1 FROM document_signers s
            WHERE s.document_id = o.id AND s.user_id = $1::uuid
        ) AND d.created_by != $1::uuid)
        OR (
            o.signer_sector_ids && $2::uuid[]
            AND d.created_by != $1::uuid
            AND dt.acronym NOT IN ('NOTA', 'MEMO')
        )
      )
    """

    _vis_draft_propios = """
        SELECT d1.id
          FROM document_draft d1
         WHERE d1.created_by = $1::uuid
           AND d1.is_deleted = false
        UNION
        SELECT ds1.document_id AS id
          FROM document_signers ds1
          JOIN document_draft d2 ON d2.id = ds1.document_id
         WHERE ds1.user_id = $1::uuid
           AND d2.is_deleted = false
           AND d2.created_by != $1::uuid
           AND d2.status IN ('sent_to_sign', 'signed')"""

    _vis_draft_sector = """
        SELECT d3.id
          FROM document_draft d3
          JOIN document_types dt3 ON dt3.id = d3.document_type_id
         WHERE d3.created_by IN (
                   SELECT u2.id FROM users u2 WHERE u2.sector_id = ANY($2::uuid[])
               )
           AND d3.is_deleted = false
           AND d3.created_by != $1::uuid
           AND d3.status IN ('sent_to_sign', 'signed')
           AND dt3.acronym NOT IN ('NOTA', 'MEMO')"""

    _vis_official_propios = """
        SELECT o1.id
          FROM official_documents o1
          JOIN document_draft d1 ON d1.id = o1.id
         WHERE d1.created_by = $1::uuid
           AND d1.is_deleted = false
           AND o1.signed_at IS NOT NULL
        UNION
        SELECT ds1.document_id AS id
          FROM document_signers ds1
          JOIN official_documents o2 ON o2.id = ds1.document_id
          JOIN document_draft d2 ON d2.id = o2.id
         WHERE ds1.user_id = $1::uuid
           AND d2.is_deleted = false
           AND d2.created_by != $1::uuid
           AND o2.signed_at IS NOT NULL"""

    _vis_official_sector = """
        SELECT o3.id
          FROM official_documents o3
          JOIN document_draft d3 ON d3.id = o3.id
          JOIN document_types dt3 ON dt3.id = o3.document_type_id
         WHERE o3.signer_sector_ids && $2::uuid[]
           AND d3.is_deleted = false
           AND d3.created_by != $1::uuid
           AND o3.signed_at IS NOT NULL
           AND dt3.acronym NOT IN ('NOTA', 'MEMO')"""

    if sector_filter == "mine":
        _vis_draft = _vis_draft_propios
        _vis_official = _vis_official_propios
    elif sector_filter == "sector":
        _vis_draft = _vis_draft_sector
        _vis_official = _vis_official_sector
    else:
        _vis_draft = _vis_draft_propios + "\n        UNION" + _vis_draft_sector
        _vis_official = _vis_official_propios + "\n        UNION" + _vis_official_sector

    union_cte_light = f"""
    SELECT
        d.id AS document_id,
        d.reference,
        d.status::text AS status,
        d.last_modified_at,
        dt.acronym::text AS acronym,
        dt.signature_policy::text AS signature_policy,
        dt.is_reserved AS document_type_is_reserved,
        dt.visibility::text AS document_type_visibility,
        NULL::text AS official_number,
        COALESCE(signer_agg.signers_names, '') AS signers_names,
        COALESCE(signer_agg.signers_count, 0) AS signers_count,
        CASE
          WHEN ds.is_numerator = true THEN 'numerador'
          WHEN d.created_by = $1::uuid THEN 'creador'
          WHEN ds.is_numerator = false AND ds.user_id IS NOT NULL THEN 'firmante'
          WHEN EXISTS (SELECT 1 FROM users u2 WHERE u2.id = d.created_by AND u2.sector_id = ANY($2::uuid[])) THEN 'sector'
          ELSE 'otro'
        END AS rol_usuario,
        ds.signed_at IS NOT NULL AS usuario_ya_firmo,
        ds.user_id IS NOT NULL AS usuario_es_firmante,
        -- GDI-366: "es mi turno de firmar". Replica `_is_my_turn_condition`
        -- (services/documents/retrieval/pending_signatures.py:44-56), que es lo que
        -- YA usa la pantalla de INICIO y lo mismo que decide si el boton Firmar se
        -- habilita (`_can_user_sign`, details_builder.py:541-565).
        -- El NOT EXISTS busca firmantes que ME BLOQUEAN:
        --   * si soy numerador -> me bloquea CUALQUIER firmante comun pendiente
        --     (el numerador firma al final);
        --   * si soy comun -> solo me bloquean los comunes de signing_order MENOR;
        --     el numerador nunca bloquea a un comun.
        -- Antes de GDI-366 el listado ignoraba `signing_order`, asi que ponia
        -- "Firmar ahora" arriba de todo en documentos cuyo boton estaba
        -- deshabilitado. Dos pantallas decian cosas distintas del mismo documento.
        CASE WHEN ds.user_id IS NULL THEN false ELSE NOT EXISTS (
          SELECT 1 FROM document_signers ds2
          WHERE ds2.document_id = d.id
            AND ds2.status = 'pending'
            AND ds2.is_numerator = false
            AND (ds.is_numerator = true OR ds2.signing_order < ds.signing_order)
        ) END AS es_mi_turno,
        COALESCE(
          (SELECT bool_and(s2.signed_at IS NOT NULL)
           FROM document_signers s2
           WHERE s2.document_id = d.id AND (s2.is_numerator = false OR s2.is_numerator IS NULL)),
          true
        ) AS todos_firmantes_comunes_firmaron
    FROM document_draft d
    JOIN document_types dt ON d.document_type_id = dt.id
    -- PERF (carga 21/08): los permisos se resuelven POR INDICE, no filtrando un
    -- scan completo. El WHERE anterior era `created_by=$1 OR EXISTS(soy
    -- firmante) OR EXISTS(es de mi sector)`, y un OR sobre tablas distintas no
    -- se puede indexar: el planner escaneaba document_draft ENTERA y ejecutaba
    -- el EXISTS de firmantes UNA VEZ POR FILA CANDIDATA. Medido con EXPLAIN
    -- ANALYZE sobre 100_test (24.676 drafts): `SubPlan ... loops=25250` y
    -- `Buffers: shared hit=129988` = 1 GB leido para devolver 20 filas.
    --
    -- Ahora cada origen de permiso se pide por SU indice y se unen los ids:
    --   A1 lo cree yo               -> idx_document_draft_created_by
    --   A2 soy firmante             -> idx_doc_signers_user
    --   A3 lo creo alguien de mi sector -> idx_document_draft_created_by
    -- UNION (no ALL) deduplica: un mismo documento puede entrar por varios
    -- origenes. La condicion de cada rama es identica a la del OR que
    -- reemplaza, incluidos los `created_by != $1` y el `NOT IN ('NOTA','MEMO')`.
    --
    -- Paridad verificada fila-a-fila Y en orden contra el camino viejo
    -- (tests/test_document_service_pagination_rewrite.py compara contra
    -- _force_single_query_fetch, que usa union_cte y NO se toco).
    JOIN ({_vis_draft}
    ) vis ON vis.id = d.id
    LEFT JOIN document_signers ds ON ds.document_id = d.id AND ds.user_id = $1::uuid
    LEFT JOIN LATERAL (
        SELECT
            string_agg(COALESCE(signer_u.full_name, signer_c.full_name), ' ' ORDER BY signer_ds.signing_order NULLS LAST, signer_ds.user_id, signer_ds.citizen_id) AS signers_names,
            COUNT(*) AS signers_count
        FROM document_signers signer_ds
        -- GDI-130 (TAD): ver comentario equivalente en union_cte (firmantes ciudadano).
        LEFT JOIN users signer_u ON signer_u.id = signer_ds.user_id
        LEFT JOIN citizens signer_c ON signer_c.id = signer_ds.citizen_id
        WHERE signer_ds.document_id = d.id
    ) signer_agg ON true
    WHERE dt.is_active = true
      AND d.is_deleted = false
      AND NOT EXISTS (SELECT 1 FROM official_documents WHERE id = d.id AND signed_at IS NOT NULL)

    UNION ALL

    SELECT
        o.id AS document_id,
        o.reference,
        'signed'::text AS status,
        o.signed_at AS last_modified_at,
        dt.acronym::text AS acronym,
        dt.signature_policy::text AS signature_policy,
        dt.is_reserved AS document_type_is_reserved,
        dt.visibility::text AS document_type_visibility,
        o.official_number::text AS official_number,
        COALESCE(signer_agg.signers_names, '') AS signers_names,
        COALESCE(signer_agg.signers_count, 0) AS signers_count,
        CASE
          WHEN ds.is_numerator THEN 'numerador'
          WHEN ds.user_id IS NOT NULL THEN 'firmante'
          WHEN d.created_by = $1::uuid THEN 'creador'
          WHEN o.signer_sector_ids && $2::uuid[] THEN 'sector'
          ELSE 'otro'
        END AS rol_usuario,
        ds.signed_at IS NOT NULL AS usuario_ya_firmo,
        ds.user_id IS NOT NULL AS usuario_es_firmante,
        -- GDI-366: la columna tiene que existir en las DOS ramas del UNION. En un
        -- documento oficial ya firmaron todos, asi que el turno no aplica nunca.
        false AS es_mi_turno,
        (SELECT bool_and(s2.signed_at IS NOT NULL)
         FROM document_signers s2
         WHERE s2.document_id = o.id AND (s2.is_numerator = false OR s2.is_numerator IS NULL)
        ) AS todos_firmantes_comunes_firmaron
    FROM official_documents o
    JOIN document_draft d ON o.id = d.id
    JOIN document_types dt ON o.document_type_id = dt.id
    -- PERF (carga 21/08): mismo tratamiento que la rama de borradores — cada
    -- origen de permiso por su indice, en vez de un OR que fuerza scan completo
    -- de official_documents (17.648 filas) con un EXISTS por fila.
    --   B1 lo cree yo        -> idx_document_draft_created_by
    --   B2 soy firmante      -> idx_doc_signers_user
    --   B3 firmo mi sector   -> idx_official_docs_signer_sectors (GIN, ya existia)
    JOIN ({_vis_official}
    ) vis ON vis.id = o.id
    LEFT JOIN document_signers ds ON ds.document_id = o.id AND ds.user_id = $1::uuid
    LEFT JOIN LATERAL (
        SELECT
            string_agg(COALESCE(signer_u.full_name, signer_c.full_name), ' ' ORDER BY signer_ds.signing_order NULLS LAST, signer_ds.user_id, signer_ds.citizen_id) AS signers_names,
            COUNT(*) AS signers_count
        FROM document_signers signer_ds
        -- GDI-130 (TAD): ver comentario equivalente en union_cte (firmantes ciudadano).
        LEFT JOIN users signer_u ON signer_u.id = signer_ds.user_id
        LEFT JOIN citizens signer_c ON signer_c.id = signer_ds.citizen_id
        WHERE signer_ds.document_id = o.id
    ) signer_agg ON true
    WHERE dt.is_active = true
      AND d.is_deleted = false
      AND o.signed_at IS NOT NULL
    """

    filter_conditions: List[str] = []
    filter_params: List[Any] = []

    next_param = [3]

    def add_param(value: Any) -> str:
        placeholder = f"${next_param[0]}"
        filter_params.append(value)
        next_param[0] += 1
        return placeholder

    if status_filter == "En edición":
        filter_conditions.append("status IN ('draft', 'rejected')")
    elif status_filter == "Firmado":
        filter_conditions.append("status = 'signed'")
    elif status_filter == "Firmar ahora":
        if signature_mode == "digital":
            filter_conditions.append(f"""(
                {_ES_ACCIONABLE_SQL}
                AND (
                    signature_policy = 'digital_all'
                    OR (signature_policy = 'digital_num' AND rol_usuario = 'numerador')
                )
            )""")
        else:
            filter_conditions.append(f"""(
                {_ES_ACCIONABLE_SQL}
                AND (
                    signature_policy = 'electronic'
                    OR (signature_policy = 'digital_num' AND rol_usuario != 'numerador')
                )
            )""")
    elif status_filter == "A mi firma":
        filter_conditions.append(f"({_ES_ACCIONABLE_SQL})")
    elif status_filter == "En proceso de firma":
        filter_conditions.append(f"""(
            status = 'sent_to_sign'
            AND NOT ({_ES_ACCIONABLE_SQL})
        )""")

    if sector_filter == "mine":
        filter_conditions.append("rol_usuario != 'sector'")
    elif sector_filter == "sector":
        filter_conditions.append("rol_usuario = 'sector'")

    if date_filter == "hoy":
        filter_conditions.append(
            "last_modified_at >= DATE_TRUNC('day', NOW() AT TIME ZONE 'UTC')"
            " AND last_modified_at < DATE_TRUNC('day', NOW() AT TIME ZONE 'UTC') + INTERVAL '1 day'"
        )
    elif date_filter == "ayer":
        filter_conditions.append(
            "last_modified_at >= DATE_TRUNC('day', NOW() AT TIME ZONE 'UTC') - INTERVAL '1 day'"
            " AND last_modified_at < DATE_TRUNC('day', NOW() AT TIME ZONE 'UTC')"
        )
    elif date_filter == "ultimos_7_dias":
        filter_conditions.append(
            "last_modified_at >= DATE_TRUNC('day', NOW() AT TIME ZONE 'UTC') - INTERVAL '7 days'"
        )
    elif date_filter == "ultimos_30_dias":
        filter_conditions.append(
            "last_modified_at >= DATE_TRUNC('day', NOW() AT TIME ZONE 'UTC') - INTERVAL '30 days'"
        )

    if date_from_str:
        try:
            date_from = datetime.strptime(date_from_str, "%Y-%m-%d").date()
            p = add_param(date_from)
            filter_conditions.append(f"last_modified_at >= {p}::date")
        except ValueError:
            pass

    if date_to_str:
        try:
            date_to = datetime.strptime(date_to_str, "%Y-%m-%d").date()
            p = add_param(date_to)
            filter_conditions.append(f"last_modified_at < ({p}::date + INTERVAL '1 day')")
        except ValueError:
            pass

    if document_type:
        p = add_param(document_type)
        filter_conditions.append(f"acronym = {p}")

    if min_signers and min_signers > 0:
        p = add_param(min_signers)
        filter_conditions.append(f"signers_count >= {p}")

    if exclude_reserved:
        filter_conditions.append("document_type_is_reserved = false")

    if case_id:
        p = add_param(case_id)
        filter_conditions.append(f"""(
            EXISTS (
                SELECT 1 FROM case_official_documents cod
                WHERE cod.official_document_id = document_id
                  AND cod.case_id = {p}::uuid
                  AND cod.is_active = true
            )
            OR EXISTS (
                SELECT 1 FROM case_proposed_documents cpd
                WHERE cpd.document_draft_id = document_id
                  AND cpd.case_id = {p}::uuid
                  AND cpd.is_active = true
            )
        )""")

    search_text = search or doc_number
    solo_numero_y_referencia = not search and bool(doc_number)
    text_search_applied = False
    if search_text and len(search_text) >= 2:
        text_search_applied = True
        p1 = add_param(f"%{search_text}%")
        p2 = add_param(f"%{search_text}%")
        campos = [
            f"public.immutable_unaccent(LOWER(reference)) LIKE public.immutable_unaccent(LOWER({p1}))",
            f"public.immutable_unaccent(LOWER(COALESCE(official_number, ''))) LIKE public.immutable_unaccent(LOWER({p2}))",
        ]
        if not solo_numero_y_referencia:
            p3 = add_param(f"%{search_text}%")
            p4 = add_param(f"%{search_text}%")
            campos.append(
                f"public.immutable_unaccent(LOWER(content_html)) LIKE public.immutable_unaccent(LOWER({p3}))"
            )
            campos.append(
                f"public.immutable_unaccent(LOWER(signers_names)) LIKE public.immutable_unaccent(LOWER({p4}))"
            )
        filter_conditions.append("(\n            " + "\n            OR ".join(campos) + "\n        )")

    where_clause = ("WHERE " + " AND ".join(filter_conditions)) if filter_conditions else ""

    order_by = """
    ORDER BY
        last_modified_at DESC NULLS LAST
    """

    necesita_cte_pesado = text_search_applied and not solo_numero_y_referencia
    count_cte = union_cte if necesita_cte_pesado else union_cte_light

    limit_placeholder = f"${next_param[0]}"
    offset_placeholder = f"${next_param[0] + 1}"

    ids_page_query = f"""
    SELECT document_id FROM (
        {count_cte}
    ) AS docs
    {where_clause}
    {order_by}
    LIMIT {limit_placeholder} OFFSET {offset_placeholder}
    """

    hydrate_query = f"""
    SELECT * FROM (
        {union_cte}
    ) AS docs
    WHERE docs.document_id = ANY($3::uuid[])
    ORDER BY array_position($3::uuid[], docs.document_id)
    """

    contar_universo = status_filter in _STATUS_FILTERS_QUE_CUENTAN_UNIVERSO
    count_query = f"""
    SELECT COUNT(*) FROM (
        {count_cte}
    ) AS docs
    {where_clause}
    """ if contar_universo else None

    async with get_conn(schema_name=schema_name) as conn:
        if user_sectors_list is None:
            from services.case_service import CaseService
            user_viewable_sectors = await CaseService.get_user_viewable_sector_ids(
                user_id, schema_name=schema_name, conn=conn
            )
            user_sectors_list = user_viewable_sectors if user_viewable_sectors else []

        base_params = [user_id, user_sectors_list]
        ids_final_params = base_params + filter_params + [page_size + 1, offset]
        count_final_params = base_params + filter_params

        await conn.execute("SET LOCAL jit = off")

        id_rows = await conn.fetch(ids_page_query, *ids_final_params)

        has_next = len(id_rows) > page_size
        if has_next:
            id_rows = id_rows[:page_size]

        total_docs: Optional[int] = None
        if count_query is not None:
            count_row = await conn.fetchrow(count_query, *count_final_params)
            total_docs = int(count_row[0]) if count_row else 0

        if not id_rows:
            return {
                "total": total_docs,
                "page": page,
                "page_size": page_size,
                "total_pages": (
                    (total_docs + page_size - 1) // page_size
                    if total_docs is not None else None
                ),
                "has_next": False,
                "has_previous": page > 1,
                "documents": [],
            }

        page_ids = [row["document_id"] for row in id_rows]
        raw_docs = await conn.fetch(hydrate_query, user_id, user_sectors_list, page_ids)

    paginated_docs = []
    for row in raw_docs:
        doc_dict = dict(row)
        doc_dict["display_status"] = map_display_status(
            doc_dict["status"],
            doc_dict["rol_usuario"],
            doc_dict["usuario_ya_firmo"],
            doc_dict["todos_firmantes_comunes_firmaron"],
            doc_dict["usuario_es_firmante"],
            doc_dict["document_id"],
            doc_dict.get("es_mi_turno"),
        )
        paginated_docs.append(doc_dict)

    return {
        "total": total_docs,
        "page": page,
        "page_size": page_size,
        "total_pages": (
            (total_docs + page_size - 1) // page_size
            if total_docs is not None else None
        ),
        "has_next": has_next,
        "has_previous": page > 1,
        "documents": paginated_docs,
    }
