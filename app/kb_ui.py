"""Knowledge-base explorer for the embedded Chroma index (/kb). Not mounted when APP_ENV=prod."""
from pathlib import Path

from fastapi import APIRouter, Query
from fastapi.responses import HTMLResponse

from . import rag

router = APIRouter(prefix="/kb", include_in_schema=False)
PAGE = (Path(__file__).parent / "static" / "kb.html").read_text()


@router.get("", response_class=HTMLResponse)
def page():
    return PAGE


@router.get("/api/protocols")
def protocols():
    return rag.list_protocols()


@router.get("/api/search")
def search(q: str = Query(min_length=1, max_length=2000), k: int = Query(5, ge=1, le=15)):
    return {"query": q, "results": rag.retrieve_protocols(q, k)}
