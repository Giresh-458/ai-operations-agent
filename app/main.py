from fastapi import FastAPI, Request
from app.api.routes import router
from app.database import Base, engine
from fastapi.staticfiles import StaticFiles
from fastapi.responses import RedirectResponse, JSONResponse
import os
import secrets
from app.config import settings

from sqlalchemy import text

# pgvector is available in PostgreSQL deployments, but SQLite is used by tests.
if engine.dialect.name == "postgresql":
    with engine.connect() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        conn.commit()

# Create tables
Base.metadata.create_all(bind=engine)

# Auto-seed initial data if fresh database
try:
    from scripts.seed_database import seed_data
    seed_data()
except Exception as e:
    print(f"Database seed notice: {e}")

from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(title="AI Operations Automation Agent", version="1.0.0")

@app.middleware("http")
async def require_api_token(request: Request, call_next):
    if request.url.path == "/health" or request.method == "OPTIONS":
        return await call_next(request)
    configured_token = settings.API_ACCESS_TOKEN
    if not configured_token:
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
