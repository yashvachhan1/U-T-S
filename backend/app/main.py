"""FastAPI entry point.  Serves the JSON API and the built React dashboard."""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import config
from .engine import runner
from .routers.api import router as api_router
from .upstox import instruments

DIST = config.FRONTEND_DIR / "dist"
INDEX = DIST / "index.html"


@asynccontextmanager
async def lifespan(app: FastAPI):
    if not instruments.loaded():
        print("[startup] Instrument list missing -- dashboard se refresh icon dabayein.")
    else:
        print(f"[startup] {len(instruments.all_instruments())} instruments ready.")
    mode = "PAPER (safe)" if config.PAPER_TRADING else "*** LIVE -- REAL MONEY ***"
    print(f"[startup] Trading mode: {mode}")
    if not INDEX.exists():
        print("[startup] frontend/dist missing -- 'npm run build' chalayein (ya setup.bat).")
    yield
    await runner.stop_all()


app = FastAPI(title="Upstox Trading Desk", version="2.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    # 5173 is the Vite dev server, used while developing the React app.
    allow_origins=[
        "http://localhost:8000", "http://127.0.0.1:8000",
        "http://localhost:5173", "http://127.0.0.1:5173",
    ],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router)


# The bundle filenames are content-hashed, but index.html must never be cached
# or a rebuild would keep loading the previous bundle.
@app.middleware("http")
async def no_cache_index(request, call_next):
    response = await call_next(request)
    if request.url.path in ("/", "/index.html"):
        response.headers["Cache-Control"] = "no-store, max-age=0"
    return response


def _missing_build() -> JSONResponse:
    return JSONResponse(
        status_code=503,
        content={
            "detail": "Frontend build nahi mila. frontend/ folder me 'npm install && "
                      "npm run build' chalayein, ya setup.bat dobara chalayein."
        },
    )


@app.get("/")
async def index():
    if not INDEX.exists():
        return _missing_build()
    return FileResponse(INDEX, headers={"Cache-Control": "no-store, max-age=0"})


if DIST.exists():
    # html=True makes unknown paths fall back to index.html.
    app.mount("/", StaticFiles(directory=DIST, html=True), name="static")
