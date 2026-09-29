"""FastAPI application. Run with:  uvicorn app.main:app --reload"""
import html
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, RedirectResponse

from app import admin, webhooks
from app.config import settings
from app.db import init_db

logging.basicConfig(level=logging.INFO)

KIOSK_TEMPLATE = (Path(__file__).parent / "static" / "kiosk.html").read_text(encoding="utf-8")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    yield


app = FastAPI(title="Customer Support Ticket Triage Agent", lifespan=lifespan)
app.include_router(webhooks.router)
app.include_router(admin.router)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/kiosk", response_class=HTMLResponse)
def kiosk() -> str:
    return KIOSK_TEMPLATE.replace("{{STORE_NAME}}", html.escape(settings.store_name))


@app.get("/", include_in_schema=False)
def root() -> RedirectResponse:
    return RedirectResponse("/kiosk")
