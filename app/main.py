from fastapi import FastAPI, Request
from app.api.routes import router
from app.database import Base, engine
from fastapi.staticfiles import StaticFiles
from fastapi.responses import RedirectResponse, JSONResponse
from fastapi.openapi.utils import get_openapi
import os
import secrets
from app.config import settings
from sqlalchemy import text

# pgvector is available in PostgreSQL deployments, while SQLite is used by tests.
if engine.dialect.name == "postgresql":
    with engine.begin() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))

# Initialize the demo schema and seed data. Fail startup if initialization fails;
# serving traffic with a partially initialized database hides a real deployment error.
Base.metadata.create_all(bind=engine)
# Demo fixtures are seeded explicitly by local/dev scripts, never during API startup.

from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(title="AI Operations Automation Agent", version="1.0.0")


def custom_openapi():
    if app.openapi_schema:
        return app.openapi_schema
    schema = get_openapi(title=app.title, version=app.version, routes=app.routes)
    schema.setdefault("components", {}).setdefault("securitySchemes", {})["BearerAuth"] = {
        "type": "http", "scheme": "bearer", "bearerFormat": "API token"
    }
    for path, operations in schema.get("paths", {}).items():
        if path in {"/health", "/ready"}:
            continue
        for operation in operations.values():
            if isinstance(operation, dict):
                operation["security"] = [{"BearerAuth": []}]
    app.openapi_schema = schema
    return app.openapi_schema


app.openapi = custom_openapi


@app.middleware("http")
async def require_api_token(request: Request, call_next):
    path = request.url.path
    # Public liveness/readiness probes and static UI assets are safe to fetch
    # without the API token. All data and operation endpoints remain protected.
    public_path = (
        path in {"/", "/health", "/ready", "/docs", "/openapi.json", "/redoc", "/app"}
        or path.startswith("/app/")
    )
    if public_path or request.method == "OPTIONS":
        return await call_next(request)

    configured_token = settings.API_ACCESS_TOKEN
    if (
        not configured_token
        or configured_token.lower().startswith("replace_")
        or len(configured_token) < 32
    ):
        return JSONResponse(status_code=503, content={"detail": "API_ACCESS_TOKEN must be configured"})

    supplied_token = request.headers.get("Authorization", "")
    expected_token = "Bearer " + configured_token
    if not secrets.compare_digest(supplied_token, expected_token):
        return JSONResponse(status_code=401, content={"detail": "Authentication required"})
    return await call_next(request)


app.add_middleware(
    CORSMiddleware,
    allow_origins=[origin.strip() for origin in settings.CORS_ORIGINS.split(",") if origin.strip()],
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)

app.include_router(router)


@app.get("/ready", include_in_schema=True)
def readiness_check():
    """Readiness probe: verify the database is reachable before receiving traffic."""
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception:
        return JSONResponse(status_code=503, content={"status": "not_ready", "detail": "Database unavailable"})
    return {"status": "ready"}


if os.path.exists("frontend"):
    app.mount("/app", StaticFiles(directory="frontend", html=True), name="frontend")

    @app.get("/")
    def read_root():
        return RedirectResponse(url="/app/index.html")
else:
    @app.get("/")
    def read_root():
        return {"message": "AI Operations Automation Agent API"}


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8080))
    uvicorn.run(app, host="0.0.0.0", port=port)
