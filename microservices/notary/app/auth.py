import hmac

from fastapi import HTTPException, Security
from fastapi.security import APIKeyHeader
from .config import API_KEY

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

async def validate_api_key(api_key: str = Security(api_key_header)):
    if api_key is None:
        raise HTTPException(
            status_code=401,
            detail="Missing API key",
            headers={"error_code": "MISSING_API_KEY"}
        )
    if not hmac.compare_digest(api_key, API_KEY):
        raise HTTPException(
            status_code=401,
            detail="Invalid API key",
            headers={"error_code": "INVALID_API_KEY"}
        )
    return api_key