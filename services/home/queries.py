
from services.shared.viewable_cases import get_viewable_cases_cte


def get_unread_memo_items_query() -> str:
    return """
        SELECT
            od.id as document_id,
            od.official_number,
            od.reference,
            od.resume as ai_summary,
            od.short_resume as short_ai_summary,
            od.signed_at,
            u_creator.full_name as creator_name,
            u_creator.profile_picture_url as creator_photo
        FROM memo_recipients mr
        JOIN official_documents od ON od.id = mr.document_id AND od.signed_at IS NOT NULL
        LEFT JOIN document_draft dd ON dd.id = od.id
        LEFT JOIN users u_creator ON dd.created_by = u_creator.id
        WHERE mr.recipient_user_id = $1 AND mr.is_archived = false AND mr.opened_at IS NULL
        ORDER BY od.signed_at DESC
        LIMIT $2
    """


def get_unread_memo_count_query() -> str:
    return """
        SELECT COUNT(*) AS total
        FROM memo_recipients mr
        JOIN official_documents od ON od.id = mr.document_id AND od.signed_at IS NOT NULL
        WHERE mr.recipient_user_id = $1 AND mr.is_archived = false AND mr.opened_at IS NULL
    """


def get_unread_notes_for_sectors_query() -> str:
    return """
        SELECT * FROM (
            SELECT DISTINCT ON (od.id)
                od.id as document_id,
                od.official_number,
                od.reference,
                od.resume as ai_summary,
                od.short_resume as short_ai_summary,
                od.signed_at,
                nr.sector_id,
                s.acronym as sector_acronym,
                d.acronym as department_acronym
            FROM notes_recipients nr
            JOIN official_documents od ON od.id = nr.document_id AND od.signed_at IS NOT NULL
            JOIN sectors s ON s.id = nr.sector_id
            JOIN departments d ON d.id = s.department_id
            WHERE nr.sector_id = ANY($1::uuid[])
              AND nr.is_archived = false
              AND NOT EXISTS (
                  SELECT 1 FROM notes_openings no
                  WHERE no.document_id = nr.document_id
                    AND no.sector_id = nr.sector_id
              )
            ORDER BY od.id, od.signed_at DESC
        ) sub
        ORDER BY signed_at DESC
        LIMIT $2
    """


def get_unread_notes_count_query() -> str:
    return """
        SELECT COUNT(DISTINCT od.id) AS total
        FROM notes_recipients nr
        JOIN official_documents od ON od.id = nr.document_id AND od.signed_at IS NOT NULL
        JOIN sectors s ON s.id = nr.sector_id
        JOIN departments d ON d.id = s.department_id
        WHERE nr.sector_id = ANY($1::uuid[])
          AND nr.is_archived = false
          AND NOT EXISTS (
              SELECT 1 FROM notes_openings no
              WHERE no.document_id = nr.document_id
                AND no.sector_id = nr.sector_id
          )
    """


def get_responsible_notifications_query() -> str:
    return """
        SELECT
            cm.id as movement_id,
            cm.case_id,
            cm.reason,
            cm.created_at,
            c.case_number,
            c.reference as case_reference,
            ct.acronym as case_type,
            u.full_name as actor_name,
            u.profile_picture_url as actor_photo
        FROM case_movements cm
        JOIN cases c ON c.id = cm.case_id
        JOIN case_templates ct ON ct.id = c.case_template_id
        LEFT JOIN users u ON cm.user_id = u.id
        WHERE cm.type = 'responsible_add'
          AND cm.assigned_user_id = $1
          AND cm.is_active = false
          AND NOT EXISTS (
              SELECT 1 FROM notification_dismissals nd
              WHERE nd.user_id = $1
                AND nd.notification_key = 'responsible:' || cm.id::text
          )
        ORDER BY cm.created_at DESC
    """


def get_failed_signature_notifications_query() -> str:
    return """
        SELECT * FROM (
        SELECT DISTINCT ON (ss.document_id)
            ss.session_id,
            ss.document_id,
            ss.failure_reason,
            ss.updated_at,
            dd.reference AS document_reference
        FROM public.signing_sessions ss
        LEFT JOIN document_draft dd ON dd.id = ss.document_id
        WHERE ss.user_id = $1
          AND ss.schema_name = $2
          AND ss.status IN ('failed', 'expired')
          AND ss.updated_at > NOW() - INTERVAL '7 days'
          AND COALESCE(ss.failure_reason, '') <> ALL (ARRAY[
              'superseded',
              'document_already_signing',
              'duplicate_sign_common_session_gdi215',
              'duplicate_sign_citizen_session_gdi205',
              -- GDI-362: el documento fue RECHAZADO mientras la firma estaba en
              -- la cola. No vuelve a "Esperando tu firma" (queda `rejected`), y
              -- el usuario ya se enteró del rechazo por su propio camino: acá el
              -- aviso mentiría dos veces (promete una bandeja donde no está y su
              -- link lo rebota). No hay ninguna firma que reintentar.
              'document_no_longer_signable',
              'confirmed_and_rejected_conflict'
          ])
          AND NOT EXISTS (
              SELECT 1 FROM notification_dismissals nd
              WHERE nd.user_id = $1
                AND nd.notification_key = 'signature_failed:' || ss.session_id::text
          )
          -- GDI-362 (se apaga al firmarse): el aviso vive mientras el documento
          -- siga esperando la firma de ESTE usuario. Mismo par de condiciones
          -- que la bandeja, para que no puedan discrepar.
          AND EXISTS (
              SELECT 1
              FROM document_signers ds
              JOIN document_draft d ON d.id = ds.document_id
              WHERE ds.document_id = ss.document_id
                AND ds.user_id = $1
                AND ds.status = 'pending'
                AND d.status = 'sent_to_sign'
          )
          -- GDI-362 (2 horas desde el primer visto): la fila `seen:` la escribe
          -- el front al renderizar el bloque, una sola vez (ON CONFLICT DO
          -- NOTHING), así que `dismissed_at` es el PRIMER visto y no el último.
          AND NOT EXISTS (
              SELECT 1 FROM notification_dismissals nd
              WHERE nd.user_id = $1
                AND nd.notification_key = 'seen:signature_failed:' || ss.session_id::text
                AND nd.dismissed_at <= NOW() - INTERVAL '2 hours'
          )
        -- El ORDER BY de un DISTINCT ON lo manda el propio DISTINCT ON (tiene
        -- que empezar por document_id para elegir el más reciente de cada uno),
        -- así que el orden que ve el Home se recompone afuera: cronológico, como
        -- era antes de GDI-362.
        ORDER BY ss.document_id, ss.updated_at DESC
        ) ultimo_fallo_por_documento
        ORDER BY updated_at DESC
    """


def get_mention_notifications_query() -> str:
    return """
        SELECT
            cm.id as movement_id,
            cm.case_id,
            cm.reason,
            cm.created_at,
            c.case_number,
            c.reference as case_reference,
            ct.acronym as case_type,
            u.full_name as actor_name,
            u.profile_picture_url as actor_photo
        FROM case_movements cm
        JOIN cases c ON c.id = cm.case_id
        JOIN case_templates ct ON ct.id = c.case_template_id
        LEFT JOIN users u ON cm.user_id = u.id
        WHERE cm.type = 'comment'
          AND cm.assigned_user_id = $1
          AND cm.is_active = false
          AND NOT EXISTS (
              SELECT 1 FROM notification_dismissals nd
              WHERE nd.user_id = $1
                AND nd.notification_key = 'mention:' || cm.id::text
          )
        ORDER BY cm.created_at DESC
    """


def get_case_movements_grouped_query() -> str:
    return get_viewable_cases_cte() + """
        , user_baseline AS (
            -- crack #5: baseline resuelto DENTRO de la query (subquery no
            -- correlacionado => InitPlan, una sola ejecución). Antes venía por
            -- parámetro con un roundtrip extra del caller.
            SELECT created_at FROM users WHERE id = $3
        ),
        scoped_cases AS (
            SELECT c.id
            FROM cases c
            WHERE c.status = 'active' AND (
                ($4 = 'mine' AND EXISTS (
                    SELECT 1 FROM case_responsibles cr
                    WHERE cr.case_id = c.id AND cr.user_id = $3 AND cr.is_active = true
                ))
                OR
                ($4 = 'all' AND c.id IN (SELECT id FROM viewable_cases))
            )
        ),
        candidate_cases AS (
            SELECT
                c.id as case_id,
                c.case_number,
                c.reference as case_reference,
                c.short_ai_summary,
                ct.acronym as case_type,
                COALESCE(cuv.last_seen_at, (SELECT created_at FROM user_baseline)) as baseline_seen_at
            FROM cases c
            JOIN case_templates ct ON ct.id = c.case_template_id
            JOIN scoped_cases sc ON sc.id = c.id
            LEFT JOIN case_user_views cuv ON cuv.case_id = c.id AND cuv.user_id = $3
            -- Pre-filtro barato: descarta casos sin novedad SIN tocar case_movements
            WHERE c.last_modified_at > COALESCE(cuv.last_seen_at, (SELECT created_at FROM user_baseline))
        ),
        movement_counts AS (
            SELECT
                cc.case_id,
                cc.case_number,
                cc.case_reference,
                cc.short_ai_summary,
                cc.case_type,
                COUNT(cm.id) as new_count,
                MAX(cm.created_at) as last_move_at
            FROM candidate_cases cc
            JOIN case_movements cm
                ON cm.case_id = cc.case_id
               AND cm.created_at > cc.baseline_seen_at
               AND cm.user_id <> $3
               AND cm.type NOT IN ('responsible_add', 'responsible_remove')
               AND NOT (cm.type = 'comment' AND cm.assigned_user_id = $3)
            GROUP BY cc.case_id, cc.case_number, cc.case_reference, cc.short_ai_summary, cc.case_type
            HAVING COUNT(cm.id) > 0
        )
        SELECT *
        FROM movement_counts
        WHERE ($5::timestamptz IS NULL)
           OR (last_move_at, case_id) < ($5::timestamptz, $6::uuid)
        ORDER BY last_move_at DESC, case_id DESC
        LIMIT $7
    """


def get_unassigned_unowned_query() -> str:
    return get_viewable_cases_cte() + """
        SELECT
            c.id as case_id,
            c.case_number,
            c.reference as case_reference,
            c.short_ai_summary,
            c.created_at,
            ct.acronym as case_type
        FROM cases c
        JOIN case_templates ct ON ct.id = c.case_template_id
        WHERE c.id IN (SELECT id FROM viewable_cases)
          AND c.status = 'active'
          AND NOT EXISTS (
              SELECT 1 FROM case_responsibles cr
              WHERE cr.case_id = c.id AND cr.is_active = true
          )
        ORDER BY c.last_modified_at DESC
        LIMIT $3
    """


def get_unassigned_unowned_count_query() -> str:
    return get_viewable_cases_cte() + """
        SELECT COUNT(*)::int as total
        FROM cases c
        WHERE c.id IN (SELECT id FROM viewable_cases)
          AND c.status = 'active'
          AND NOT EXISTS (
              SELECT 1 FROM case_responsibles cr
              WHERE cr.case_id = c.id AND cr.is_active = true
          )
    """


def get_unassigned_tasks_query() -> str:
    return """
        SELECT
            cat.id as task_id,
            cat.case_id,
            cat.reason,
            cat.created_at,
            c.case_number,
            c.reference as case_reference,
            c.short_ai_summary,
            ct.acronym as case_type
        FROM case_assignment_tasks cat
        JOIN cases c ON c.id = cat.case_id
        JOIN case_templates ct ON ct.id = c.case_template_id
        WHERE cat.assigned_sector_id = ANY($1::uuid[])
          AND cat.status = 'open'
          AND cat.assigned_user_id IS NULL
        ORDER BY cat.created_at DESC
        LIMIT $2
    """


def get_unassigned_tasks_count_query() -> str:
    return """
        SELECT COUNT(*)::int as total
        FROM case_assignment_tasks cat
        WHERE cat.assigned_sector_id = ANY($1::uuid[])
          AND cat.status = 'open'
          AND cat.assigned_user_id IS NULL
    """


def upsert_case_user_view_query() -> str:
    return """
        INSERT INTO case_user_views (user_id, case_id, last_seen_at)
        VALUES ($1, $2, NOW())
        ON CONFLICT (user_id, case_id) DO UPDATE SET last_seen_at = NOW()
    """


def insert_notification_dismissal_query() -> str:
    return """
        INSERT INTO notification_dismissals (user_id, notification_key)
        VALUES ($1, $2)
        ON CONFLICT (user_id, notification_key) DO NOTHING
    """


def dismiss_case_notifications_on_view_query() -> str:
    return """
        INSERT INTO notification_dismissals (user_id, notification_key)
        SELECT
            $1,
            CASE cm.type
                WHEN 'responsible_add' THEN 'responsible:'
                ELSE 'mention:'
            END || cm.id::text
        FROM case_movements cm
        WHERE cm.case_id = $2
          AND cm.assigned_user_id = $1
          AND cm.type IN ('responsible_add', 'comment')
          AND cm.is_active = false
        ON CONFLICT (user_id, notification_key) DO NOTHING
    """


def get_case_exists_query() -> str:
    return """
        SELECT 1 FROM cases WHERE id = $1
    """
