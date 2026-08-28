from dataclasses import dataclass


@dataclass
class PollSigningPending:
    pass


@dataclass
class PollSigningSigned:
    signed_pdf_bytes: bytes
    cert_der: bytes


@dataclass
class PollSigningCancelled:
    pass


@dataclass
class PollSigningFailed:
    error_code: str
    error_message: str


@dataclass
class PollSigningExpired:
    pass
