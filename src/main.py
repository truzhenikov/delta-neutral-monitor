from __future__ import annotations

import logging

from fastapi import FastAPI

from src.api.routes import router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)

app = FastAPI(title="Delta Neutral Monitor", version="0.1.0")
app.include_router(router)
