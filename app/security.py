import hmac
import hashlib

from fastapi import Request
from fastapi.responses import JSONResponse

from app.config import get_settings

settings = get_settings()
TOKEN_NAME = "pp_session"


def make_token() -> str:
    return hmac.new(
        settings.secret_key.encode(),
        settings.admin_password.encode(),
        hashlib.sha256,
    ).hexdigest()


def check_auth(request: Request) -> bool:
    token = request.cookies.get(TOKEN_NAME, "")
    return bool(token) and hmac.compare_digest(token, make_token())


def verify_password(password: str) -> bool:
    if not settings.admin_password:
        return False
    return hmac.compare_digest(password.encode(), settings.admin_password.encode())


def require_auth(request: Request):
    if not check_auth(request):
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
