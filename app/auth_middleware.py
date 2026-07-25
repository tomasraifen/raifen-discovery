"""Login basico HTTP global (protege TODAS las rutas admin/curaduria). /r/<token> es
publica -- el participante la abre desde su link personal, seguridad = el token."""
import base64
import secrets

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from . import settings

PUBLIC_PREFIXES = ("/r/", "/health", "/static/")


class BasicAuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if request.url.path.startswith(PUBLIC_PREFIXES):
            return await call_next(request)
        auth = request.headers.get("authorization", "")
        if auth.startswith("Basic "):
            try:
                decoded = base64.b64decode(auth[6:]).decode("utf-8")
                user, _, pw = decoded.partition(":")
            except Exception:
                user, pw = "", ""
            user_ok = secrets.compare_digest(user, settings.BASIC_AUTH_USER)
            pass_ok = secrets.compare_digest(pw, settings.BASIC_AUTH_PASS)
            if user_ok and pass_ok:
                return await call_next(request)
        return Response(
            content="Autenticación requerida", status_code=401, headers={"WWW-Authenticate": "Basic"},
        )
