
import re
from shared.logging import get_logger
from typing import Dict, Any, List, Optional
from database import fetch_all, fetch_val
from services.users.queries import (
    search_users_by_name_query,
    count_users_by_name_query,
    search_user_by_email_query,
    get_users_by_ids_query
)
from config.constants import (
    SEARCH_QUERY_REQUIRED_ERROR,
    SEARCH_QUERY_MIN_LENGTH_ERROR,
    SEARCH_QUERY_MAX_LENGTH_ERROR,
    SEARCH_LIMIT_INVALID_ERROR,
    SEARCH_LIMIT_MAX_ERROR,
    SEARCH_EMAIL_INVALID_FORMAT_ERROR,
    SEARCH_MIN_LENGTH,
    SEARCH_MAX_LENGTH,
    SEARCH_MAX_LIMIT
)
from shared.exceptions import ValidationError

logger = get_logger(__name__)


async def search_users_for_autocomplete(search_query: str, limit: Optional[int] = None, *, schema_name: str, conn=None) -> Dict[str, Any]:
    logger.info(f"Iniciando búsqueda de usuarios con query: '{search_query}', limit: {limit}")

    _validate_search_parameters(search_query, limit)

    search_patterns = _build_user_search_patterns(search_query)

    users_raw_data = await _fetch_users_with_search_patterns(search_patterns, limit, schema_name=schema_name, conn=conn)

    total_count = await _count_users_with_search_patterns(search_patterns, schema_name=schema_name, conn=conn)

    formatted_users = _format_user_search_results(users_raw_data)

    logger.info(f"Búsqueda completada: {len(formatted_users)} usuarios retornados de {total_count} encontrados")

    return {
        "users": formatted_users,
        "total_found": total_count
    }

async def get_users_by_ids(ids: List[str], *, schema_name: str) -> Dict[str, Any]:
    logger.info(f"Batch fetch de {len(ids)} usuarios por id")

    try:
        users_raw_data = await fetch_all(
            get_users_by_ids_query(),
            ids,
            schema_name=schema_name
        )
    except Exception as e:
        logger.error(f"Error en batch fetch de usuarios por id: {str(e)}", exc_info=True)
        raise

    formatted_users = _format_user_search_results(users_raw_data)

    return {
        "users": formatted_users,
        "total_found": len(formatted_users)
    }


def _validate_search_parameters(search_query: str, limit: Optional[int]) -> None:
    if not search_query or not isinstance(search_query, str):
        raise ValidationError(SEARCH_QUERY_REQUIRED_ERROR)

    clean_query = search_query.strip()
    if len(clean_query) < SEARCH_MIN_LENGTH:
        raise ValidationError(SEARCH_QUERY_MIN_LENGTH_ERROR.format(min_length=SEARCH_MIN_LENGTH))

    if len(clean_query) > SEARCH_MAX_LENGTH:
        raise ValidationError(SEARCH_QUERY_MAX_LENGTH_ERROR.format(max_length=SEARCH_MAX_LENGTH))

    if limit is not None:
        if not isinstance(limit, int) or limit < 1:
            raise ValidationError(SEARCH_LIMIT_INVALID_ERROR)
        if limit > SEARCH_MAX_LIMIT:
            raise ValidationError(SEARCH_LIMIT_MAX_ERROR.format(max_limit=SEARCH_MAX_LIMIT))


def _build_user_search_patterns(search_query: str) -> Dict[str, str]:
    clean_query = search_query.strip().lower()

    return {
        "pattern_start": f"{clean_query}%",
        "pattern_word_start": f"% {clean_query}%",
        "search_term": clean_query,
        "original_query": clean_query
    }


async def _fetch_users_with_search_patterns(search_patterns: Dict[str, str], limit: Optional[int], *, schema_name: str, conn=None) -> List[Dict]:
    logger.info(f"Fetching users with search patterns: {search_patterns['original_query']}, limit: {limit}")

    try:
        query = search_users_by_name_query()
        params = (
            search_patterns["pattern_start"],
            search_patterns["pattern_word_start"],
            search_patterns["search_term"],
            limit,
        )
        if conn is not None:
            results = await conn.fetch(query, *params)
        else:
            results = await fetch_all(query, *params, schema_name=schema_name)

        logger.info(f"Found {len(results)} users matching search patterns")
        return results

    except Exception as e:
        logger.error(f"Error fetching users with search patterns: {str(e)}", exc_info=True)
        raise


async def _count_users_with_search_patterns(search_patterns: Dict[str, str], *, schema_name: str, conn=None) -> int:
    logger.info(f"Counting users with search patterns: {search_patterns['original_query']}")

    try:
        query = count_users_by_name_query()
        params = (
            search_patterns["pattern_start"],
            search_patterns["pattern_word_start"],
            search_patterns["search_term"],
        )
        if conn is not None:
            result = await conn.fetchval(query, *params)
        else:
            result = await fetch_val(query, *params, schema_name=schema_name)

        count = int(result) if result is not None else 0
        logger.info(f"Total count: {count} users")
        return count

    except Exception as e:
        logger.error(f"Error counting users with search patterns: {str(e)}", exc_info=True)
        raise


def _format_user_search_results(users_raw_data: List) -> List[Dict]:
    formatted_users = []

    for user in users_raw_data:
        formatted_user = {
            "user_id": user['user_id'],
            "full_name": user['full_name'],
            "email": user.get('email'),
            "department_acronym": user.get('department_acronym'),
            "sector_acronym": user.get('sector_acronym'),
            "sector_color": user.get('sector_color'),
            "seal_name": user.get('seal_name'),
            "profile_picture_url": user.get('profile_picture_url'),
            "is_active": bool(user.get('is_active', 1))
        }

        formatted_users.append(formatted_user)

    return formatted_users


def is_email(text: str) -> bool:
    if not text or not isinstance(text, str):
        return False

    email_pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    return bool(re.match(email_pattern, text.strip()))

async def search_or_create_user_by_email(email: str, *, schema_name: str) -> Dict[str, Any]:
    logger.info("Searching user by email")

    if not is_email(email):
        logger.warning("Invalid email format")
        raise ValidationError(SEARCH_EMAIL_INVALID_FORMAT_ERROR)

    existing_user = await _fetch_user_by_email(email.strip().lower(), schema_name=schema_name)

    if existing_user:
        logger.info(f"User found by email: {existing_user['user_id']}")
        formatted_user = _format_existing_user_for_search(existing_user)
        return {
            "users": [formatted_user],
            "total_found": 1
        }
    else:
        logger.info("User not found, returning virtual user")
        formatted_user = _format_virtual_user_for_search(email.strip().lower())
        return {
            "users": [formatted_user],
            "total_found": 1
        }

async def _fetch_user_by_email(email: str, *, schema_name: str) -> Optional[Dict]:
    logger.info("Fetching user by email from database")

    try:
        query = search_user_by_email_query()
        result = await fetch_all(query, email, schema_name=schema_name)
        row = result[0] if result else None

        if row:
            logger.info(f"User found by email: {row['user_id']}")
        else:
            logger.info("No user found with this email")

        return row

    except Exception as e:
        logger.error(f"Error fetching user by email: {str(e)}", exc_info=True)
        raise

def _format_existing_user_for_search(user_data: Dict) -> Dict[str, Any]:
    return {
        "user_id": user_data['user_id'],
        "full_name": user_data['full_name'],
        "email": user_data.get('email'),
        "department_acronym": user_data.get('department_acronym'),
        "seal_name": user_data.get('seal_name'),
        "profile_picture_url": user_data.get('profile_picture_url'),
        "is_active": bool(user_data.get('is_active', 0))
    }

def _format_virtual_user_for_search(email: str) -> Dict[str, Any]:
    return {
        "user_id": None,
        "full_name": email,
        "email": email,
        "department_acronym": None,
        "seal_name": None,
        "profile_picture_url": None,
        "is_active": False
    }
