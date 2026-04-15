from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel
from slowapi import Limiter
from slowapi.util import get_remote_address

from app.security import TOKEN_NAME, check_auth, make_token, verify_password
from app.templating import templates
from app.version import ASSET_TAG

router = APIRouter()
limiter = Limiter(key_func=get_remote_address)


class LoginForm(BaseModel):
    password: str


@router.get("/admin/login", response_class=HTMLResponse)
async def login_page(request: Request):
    if check_auth(request):
        return RedirectResponse("/admin", 302)
    return templates.TemplateResponse("login.html", {"request": request, "v": ASSET_TAG})


@router.post("/admin/login")
@limiter.limit("5/minute")
async def login(request: Request, body: LoginForm):
    if verify_password(body.password):
        resp = JSONResponse({"ok": True})
        resp.set_cookie(
            TOKEN_NAME, make_token(),
            httponly=True, secure=True, samesite="strict", max_age=86400 * 7,
        )
        return resp
    return JSONResponse({"ok": False}, status_code=401)


@router.post("/admin/logout")
async def logout():
    resp = RedirectResponse("/admin/login", 302)
    resp.delete_cookie(TOKEN_NAME)
    return resp


@router.get("/admin", response_class=HTMLResponse)
async def dashboard(request: Request):
    if not check_auth(request):
        return RedirectResponse("/admin/login", 302)
    return templates.TemplateResponse("dashboard.html", {"request": request, "v": ASSET_TAG})
