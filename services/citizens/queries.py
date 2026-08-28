
CITIZEN_COLUMNS = (
    "id, full_name, country_id, estado, created_via, "
    "validated_at, validated_by, created_at, updated_at"
)


def upsert_citizen_query() -> str:
    return f"""
        INSERT INTO citizens (full_name, country_id, estado, created_via, validated_at, validated_by)
        VALUES (
            $1, $2, $3::varchar, 'api',
            CASE WHEN $3::varchar = 'validado' THEN NOW() ELSE NULL END,
            CASE WHEN $3::varchar = 'validado' THEN 'api'::varchar ELSE NULL END
        )
        ON CONFLICT (country_id) DO UPDATE SET
            full_name = EXCLUDED.full_name,
            estado = EXCLUDED.estado,
            validated_at = CASE
                WHEN EXCLUDED.estado = 'validado' AND citizens.estado <> 'validado' THEN NOW()
                ELSE citizens.validated_at
            END,
            validated_by = CASE
                WHEN EXCLUDED.estado = 'validado' AND citizens.estado <> 'validado' THEN 'api'
                ELSE citizens.validated_by
            END,
            updated_at = NOW()
        RETURNING {CITIZEN_COLUMNS}
    """


def get_citizen_by_id_query() -> str:
    return f"SELECT {CITIZEN_COLUMNS} FROM citizens WHERE id = $1"


def get_citizen_by_country_id_query() -> str:
    return f"SELECT {CITIZEN_COLUMNS} FROM citizens WHERE country_id = $1"


def update_citizen_estado_query() -> str:
    return f"""
        UPDATE citizens
        SET estado = $2::varchar,
            validated_at = CASE WHEN $2::varchar = 'validado' THEN NOW() ELSE validated_at END,
            validated_by = CASE WHEN $2::varchar = 'validado' THEN $3::varchar ELSE validated_by END,
            updated_at = NOW()
        WHERE id = $1
        RETURNING {CITIZEN_COLUMNS}
    """
