
CITIZEN_SHARE_COLUMNS = "id, case_id, citizen_id, shared_by, shared_at, removed_by, removed_at, is_active"


def citizen_exists_query() -> str:
    return "SELECT id, estado FROM citizens WHERE id = $1"


def get_case_creator_citizen_query() -> str:
    return "SELECT created_by_citizen, initiator_citizen_id FROM cases WHERE id = $1"


def get_case_admin_sector_query() -> str:
    return """
        SELECT admin_sector_id FROM case_movements
        WHERE case_id = $1 AND type IN ('creation', 'transfer')
        ORDER BY created_at DESC LIMIT 1
    """


def get_user_sector_and_name_query() -> str:
    return "SELECT sector_id, full_name FROM users WHERE id = $1"


def get_citizen_name_and_country_id_query() -> str:
    return "SELECT full_name, country_id FROM citizens WHERE id = $1"


def get_active_share_query() -> str:
    return (
        f"SELECT {CITIZEN_SHARE_COLUMNS} FROM case_citizen_shares "
        "WHERE case_id = $1 AND citizen_id = $2 AND is_active = true"
    )


def insert_citizen_share_query() -> str:
    return """
        INSERT INTO case_citizen_shares (id, case_id, citizen_id, shared_by, shared_at, is_active)
        VALUES ($1, $2, $3, $4, NOW(), true)
    """


def unshare_query() -> str:
    return """
        UPDATE case_citizen_shares
        SET is_active = false, removed_by = $1, removed_at = NOW()
        WHERE case_id = $2 AND citizen_id = $3 AND is_active = true
        RETURNING id
    """


def list_shares_of_case_query() -> str:
    return """
        SELECT
            ccs.id,
            ccs.citizen_id,
            c.full_name,
            c.country_id,
            c.estado AS citizen_estado,
            ccs.shared_by,
            ccs.shared_at
        FROM case_citizen_shares ccs
        JOIN citizens c ON c.id = ccs.citizen_id
        WHERE ccs.case_id = $1 AND ccs.is_active = true
        ORDER BY ccs.shared_at ASC
    """


def list_cases_shared_with_citizen_query() -> str:
    return """
        SELECT
            c.id AS case_id,
            c.case_number,
            c.reference,
            c.status,
            ct.type_name AS template_name,
            ct.acronym AS template_acronym,
            ccs.shared_at
        FROM case_citizen_shares ccs
        JOIN cases c ON c.id = ccs.case_id
        JOIN case_templates ct ON ct.id = c.case_template_id
        WHERE ccs.citizen_id = $1 AND ccs.is_active = true
        ORDER BY ccs.shared_at DESC
    """
