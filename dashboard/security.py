import os
import time
from collections import defaultdict, deque
from fastapi import Header, HTTPException, Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse
from .secrets import prepare_runtime_config

def _load_api_token() -> str:
    env_token = os.getenv("PCE_API_TOKEN", "").strip()
    if env_token:
        return env_token

    config_path = os.getenv("PCE_CONFIG_PATH", "/app/config/config.json")
    try:
        config = prepare_runtime_config(config_path)
        token = str(((config.get("Dashboard") or {}).get("ApiToken")) or "").strip()
        if token:
            return token
    except Exception:
        pass

    return "CHANGE-ME-STRONG-TOKEN-MIN-32-CHARS"


API_TOKEN = _load_api_token()
SESSION_COOKIE = "pce_session"

def is_valid_token(token: str | None) -> bool:
    return bool(token) and token == API_TOKEN

def get_request_token(request: Request, x_api_token: str | None = None) -> str | None:
    return x_api_token or request.cookies.get(SESSION_COOKIE)

async def require_auth(request: Request, x_api_token: str = Header(None)):
    if not is_valid_token(get_request_token(request, x_api_token)):
        raise HTTPException(status_code=401, detail="Invalid or missing API token")
    return True

def has_page_access(request: Request) -> bool:
    return is_valid_token(request.cookies.get(SESSION_COOKIE))

class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, calls=120, period=60):
        super().__init__(app)
        self.calls = calls
        self.period = period
        self.buckets = defaultdict(deque)

    async def dispatch(self, request, call_next):
        if request.url.path.startswith(("/static", "/api/stream", "/healthz")):
            return await call_next(request)
        ip = request.client.host if request.client else "unknown"
        now = time.time()
        b = self.buckets[ip]
        while b and now - b[0] > self.period:
            b.popleft()
        if len(b) >= self.calls:
            return JSONResponse({"detail": "Rate limit exceeded"}, status_code=429)
        b.append(now)
        return await call_next(request)
