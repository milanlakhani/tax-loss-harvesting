from fastapi import FastAPI

from app.api.health import router

app = FastAPI(title="Tax Loss Harvesting Demo", version="0.1.0")
app.include_router(router)
