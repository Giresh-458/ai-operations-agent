from fastapi import FastAPI
from app.api.routes import router
from app.database import Base, engine
from fastapi.staticfiles import StaticFiles
from fastapi.responses import RedirectResponse
import os

from sqlalchemy import text

# Create vector extension if not exists
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

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
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
