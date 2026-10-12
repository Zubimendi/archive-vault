from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from src.config import settings
from src.database import engine, Base
from src.api.routers import (
    health,
    tenants,
    data_maps,
    subjects,
    erasure_requests,
    ledger,
    holds,
    suppression,
    coverage,
    deadlines
)

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Ensure tables and triggers are ready on startup
    yield
    await engine.dispose()

app = FastAPI(
    title=settings.APP_NAME,
    version=settings.VERSION,
    description="A GDPR/CCPA data lifecycle and erasure service featuring 3-phase sagas, envelope crypto-shredding, and Zero-PII tamper-evident proofs.",
    lifespan=lifespan
)

# Enable CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include Routers
app.include_router(health.router)
app.include_router(tenants.router)
app.include_router(data_maps.router)
app.include_router(subjects.router)
app.include_router(erasure_requests.router)
app.include_router(ledger.router)
app.include_router(holds.router)
app.include_router(suppression.router)
app.include_router(coverage.router)
app.include_router(deadlines.router)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("src.main:app", host="0.0.0.0", port=8000, reload=True)
