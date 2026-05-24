from fastapi import FastAPI
from fastapi.responses import JSONResponse

app = FastAPI(
    title="Picture-Stage",
    description="Self-hosted photo proofing for photographers and models",
    version="0.1.0",
)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
