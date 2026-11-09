"""REST-API der Dokumente – für die Oberfläche (und Skripte). Davor steht Cloudflare Access."""
import time

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import PlainTextResponse, Response
from pydantic import BaseModel, Field

from . import lookup, store

router = APIRouter(prefix="/api/v1")
MAX_MD = 2_000_000


class DocIn(BaseModel):
    title: str = Field("Ohne Titel", max_length=200)
    content: str = Field("", max_length=MAX_MD)
    settings: dict = {}
    sources: list | None = None      # None = vorhandene Quellen behalten
    tabs: list | None = None         # weitere Tabs; None = vorhandene behalten
    tabName: str | None = None       # Name des ersten Tabs


class ImportIn(BaseModel):
    name: str = Field(max_length=200)
    text: str = Field(max_length=MAX_MD)


def _check_size(body: DocIn) -> None:
    extra = sum(len(t.get("content") or "") for t in body.tabs or [] if isinstance(t, dict) and isinstance(t.get("content"), (str, type(None))))
    if len(body.content) + extra > MAX_MD:
        raise HTTPException(413, "Dokument ist zu groß")


def _need(i: str) -> dict:
    d = store.load(i) if store.valid_id(i) else None
    if not d:
        raise HTTPException(404, "Dokument nicht gefunden")
    return d


@router.get("/health")
async def health():
    return {"ok": True}


@router.get("/defaults")
async def defaults():
    return store.DEFAULTS


@router.get("/docs")
async def list_docs():
    return store.listing()


@router.post("/docs", status_code=201)
async def create(body: DocIn):
    _check_size(body)
    i = store.new_id()
    d = store.save(i, body.title, body.content, body.settings, body.sources or [], tabs=body.tabs, tab_name=body.tabName)
    return d


@router.get("/docs/{i}")
async def get_doc(i: str):
    return _need(i)


@router.put("/docs/{i}")
async def put_doc(i: str, body: DocIn):
    _need(i)
    _check_size(body)
    d = store.save(i, body.title, body.content, body.settings, body.sources, tabs=body.tabs, tab_name=body.tabName)
    return {"id": i, "updated": d["updated"]}


@router.delete("/docs/{i}", status_code=204)
async def delete_doc(i: str):
    _need(i)
    store.delete(i)
    return Response(status_code=204)


@router.get("/lookup")
async def lookup_source(q: str = Query("", max_length=500)):
    """Quelle aus Webadresse, ISBN oder DOI vorausfüllen."""
    try:
        return await lookup.lookup(q)
    except lookup.LookupFehler as e:
        raise HTTPException(400, str(e)) from e


@router.get("/backup")
async def backup():
    """Alle Dokumente als ZIP (.idadocs + .md)."""
    name = time.strftime("ida-docs-backup-%Y-%m-%d.zip")
    return Response(store.backup_zip(), media_type="application/zip", headers={"content-disposition": f'attachment; filename="{name}"'})


@router.get("/docs/{i}/export")
async def export(i: str, fmt: str = "md"):
    d = _need(i)
    if fmt == "idadocs":
        import json
        return Response(json.dumps(d, ensure_ascii=False, indent=1), media_type="application/json")
    return PlainTextResponse(store.full_md(d["content"], d.get("tabs", [])), media_type="text/markdown; charset=utf-8")


@router.post("/import", status_code=201)
async def import_doc(body: ImportIn):
    try:
        title, md, settings, sources, tabs, tab_name = store.parse_import(body.name, body.text)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    i = store.new_id()
    d = store.save(i, title, md, settings, sources, tabs=tabs, tab_name=tab_name)
    return d
