import asyncio, json, os
from pathlib import Path
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, Depends
from fastapi.responses import HTMLResponse, StreamingResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.middleware.cors import CORSMiddleware

from .db import init_db, seed_if_empty, save_run
from .sse import broadcaster
from .security import require_auth, RateLimitMiddleware, has_page_access, is_valid_token, SESSION_COOKIE
from .api import stats, users, actions, runs, control, settings_api
from .secrets import load_runtime_env

BASE_DIR = Path(__file__).parent
VERSION_PATH = BASE_DIR.parent / "VERSION"


def get_runtime_version() -> str:
    try:
        return VERSION_PATH.read_text(encoding="utf-8").strip() or "dev"
    except Exception:
        return "dev"

@asynccontextmanager
async def lifespan(app: FastAPI):
    load_runtime_env()
    init_db()
    seed_if_empty()
    print(f"PCE Dashboard v{get_runtime_version()} started on :8080")
    yield

RUNTIME_VERSION = get_runtime_version()
app = FastAPI(title="PCE Dashboard", version=RUNTIME_VERSION, lifespan=lifespan, docs_url="/api/docs")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
app.add_middleware(RateLimitMiddleware, calls=120, period=60)

app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")
templates.env.globals["default_api_token"] = (
    os.getenv("PCE_API_TOKEN", "")
    if os.getenv("PCE_EXPOSE_DEFAULT_API_TOKEN", "").lower() in ("1", "true", "yes", "on")
    else ""
)
templates.env.globals["asset_version"] = os.getenv("PCE_ASSET_VERSION", RUNTIME_VERSION)
templates.env.globals["runtime_version"] = RUNTIME_VERSION
templates.env.globals["runtime_config_path"] = os.getenv("PCE_CONFIG_PATH", "/app/config/config.json")
templates.env.globals["runtime_env_path"] = os.getenv("PCE_ENV_PATH", "/app/.env")

def _page_or_login(request: Request, template_name: str, page: str):
    if not has_page_access(request):
        return RedirectResponse(url="/login", status_code=303)
    return templates.TemplateResponse(request, template_name, {"request": request, "page": page})

@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    if has_page_access(request):
        return RedirectResponse(url="/", status_code=303)
    return templates.TemplateResponse(request, "login.html", {"request": request})

@app.post("/login")
async def login_submit(request: Request):
    payload = await request.json()
    token = str(payload.get("token") or "").strip()
    if not is_valid_token(token):
        return JSONResponse({"detail": "Invalid API token"}, status_code=401)
    response = JSONResponse({"status": "ok"})
    response.set_cookie(
        key=SESSION_COOKIE,
        value=token,
        httponly=True,
        secure=os.getenv("PCE_SESSION_COOKIE_SECURE", "").lower() in ("1", "true", "yes", "on"),
        samesite="lax",
        path="/",
    )
    return response

@app.post("/logout")
async def logout():
    response = JSONResponse({"status": "ok"})
    response.delete_cookie(SESSION_COOKIE, path="/")
    return response

@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return _page_or_login(request, "index.html", "dashboard")

@app.get("/users", response_class=HTMLResponse)
async def users_page(request: Request):
    return _page_or_login(request, "users.html", "users")

@app.get("/runs", response_class=HTMLResponse)
async def runs_page(request: Request):
    return _page_or_login(request, "runs.html", "runs")

@app.get("/actions", response_class=HTMLResponse)
async def actions_page(request: Request):
    return _page_or_login(request, "actions.html", "runs")

@app.get("/logs", response_class=HTMLResponse)
async def logs_page(request: Request):
    return _page_or_login(request, "logs.html", "logs")

@app.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request):
    return _page_or_login(request, "settings.html", "settings")

app.include_router(stats.router, prefix="/api/stats", tags=["stats"], dependencies=[Depends(require_auth)])
app.include_router(users.router, prefix="/api/users", tags=["users"], dependencies=[Depends(require_auth)])
app.include_router(actions.router, prefix="/api/actions", tags=["actions"], dependencies=[Depends(require_auth)])
app.include_router(runs.router, prefix="/api/runs", tags=["runs"], dependencies=[Depends(require_auth)])
app.include_router(control.router, prefix="/api/control", tags=["control"], dependencies=[Depends(require_auth)])
app.include_router(settings_api.router, prefix="/api/settings", tags=["settings"], dependencies=[Depends(require_auth)])

@app.get("/api/stream")
async def sse_stream(request: Request, _auth=Depends(require_auth)):
    async def gen():
        queue = broadcaster.subscribe()
        try:
            yield f"event: hello\ndata: {json.dumps({'msg':'connected'})}\n\n"
            for ev in broadcaster.history():
                yield f"event: {ev['type']}\ndata: {json.dumps(ev['data'])}\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    ev = await asyncio.wait_for(queue.get(), timeout=15.0)
                    yield f"event: {ev['type']}\ndata: {json.dumps(ev['data'])}\n\n"
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            broadcaster.unsubscribe(queue)
    return StreamingResponse(gen(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"})

@app.post("/api/ingest")
async def ingest(request: Request, _auth=Depends(require_auth)):
    payload = await request.json()
    save_run(payload)
    await broadcaster.publish("run_completed", {"run_id": payload.get("run_id"), "stats": payload.get("stats")})
    return {"status": "ok"}

@app.get("/healthz")
async def healthz():
    return {"status": "healthy", "version": RUNTIME_VERSION}
