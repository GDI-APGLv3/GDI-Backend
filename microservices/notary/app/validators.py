import re
from fastapi import HTTPException, UploadFile
from typing import Optional
from .config import (
    LETTER_WIDTH, LETTER_HEIGHT, MAX_SIGNABLE_PDF_SIZE_MB, SPECIAL_CHARS
)

TENANT_ID_PATTERN = re.compile(r'^[a-zA-Z0-9_-]+$')

class ValidationError(Exception):
    def __init__(self, message: str, error_code: str):
        self.message = message
        self.error_code = error_code
        super().__init__(self.message)

def validate_pdf_format(pdf_file: UploadFile) -> bytes:
    if pdf_file.size > MAX_SIGNABLE_PDF_SIZE_MB * 1024 * 1024:
        raise HTTPException(
            status_code=400,
            detail=f"PDF file too large. Maximum size: {MAX_SIGNABLE_PDF_SIZE_MB}MB",
            headers={"error_code": "FILE_TOO_LARGE"}
        )
    
    try:
        pdf_content = pdf_file.file.read()
        pdf_file.file.seek(0)
    except Exception as e:
        raise HTTPException(
            status_code=400,
            detail="Could not read PDF file",
            headers={"error_code": "INVALID_PDF_FILE"}
        )
    
    if not pdf_content.startswith(b'%PDF'):
        raise HTTPException(
            status_code=400,
            detail="Invalid PDF format",
            headers={"error_code": "INVALID_PDF_FORMAT"}
        )
    
    return pdf_content


def validate_signature_params(name: str, seal: str, department: str, entity: str) -> dict:
    errors = []
    
    if not name or len(name.strip()) == 0:
        errors.append("name is required")
    elif len(name) > 100:
        errors.append("name must be 100 characters or less")
    
    if not seal or len(seal.strip()) == 0:
        errors.append("seal is required")
    elif len(seal) > 50:
        errors.append("seal must be 50 characters or less")
    
    if not department or len(department.strip()) == 0:
        errors.append("department is required")
    elif len(department) > 100:
        errors.append("department must be 100 characters or less")
    
    if not entity or len(entity.strip()) == 0:
        errors.append("entity is required")
    elif len(entity) > 100:
        errors.append("entity must be 100 characters or less")
    
    if errors:
        raise HTTPException(
            status_code=400,
            detail=f"Validation errors: {', '.join(errors)}",
            headers={"error_code": "INVALID_PARAMETERS"}
        )
    
    return {
        "name": name.strip(),
        "seal": seal.strip(),
        "department": department.strip(),
        "entity": entity.strip()
    }

def validate_stamp_params(document_number: Optional[str], city: Optional[str]) -> Optional[dict]:
    if not document_number:
        return None
    
    errors = []
    
    if len(document_number) > 40:
        errors.append("document_number must be 40 characters or less")
    
    if not city or len(city.strip()) == 0:
        errors.append("city is required when document_number is provided")
    elif len(city) > 50:
        errors.append("city must be 50 characters or less")
    
    if errors:
        raise HTTPException(
            status_code=400,
            detail=f"Validation errors: {', '.join(errors)}",
            headers={"error_code": "INVALID_STAMP_PARAMETERS"}
        )
    
    return {
        "document_number": document_number.strip(),
        "city": city.strip()
    }

def sanitize_filename(filename: str) -> str:
    sanitized = filename
    for char in SPECIAL_CHARS:
        sanitized = sanitized.replace(char, '-')
    return sanitized


def validate_stamp_position(stamp_position: Optional[str]) -> None:
    if stamp_position is not None:
        valid_positions = ["first", "last"]
        if stamp_position not in valid_positions:
            raise HTTPException(
                status_code=400,
                detail=f"stamp_position must be 'first' or 'last', received: '{stamp_position}'",
                headers={"error_code": "INVALID_STAMP_POSITION"}
            )


def validate_tenant_id(tenant_id: str) -> str:
    if not tenant_id or not TENANT_ID_PATTERN.match(tenant_id):
        raise HTTPException(
            status_code=400,
            detail="Invalid tenant_id format. Only alphanumeric characters, hyphens and underscores are allowed.",
            headers={"error_code": "INVALID_TENANT_ID"}
        )
    return tenant_id
