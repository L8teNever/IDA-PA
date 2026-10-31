"""Freigabe-Proposals für Schreibzugriffe von IDA-Agents auf den privaten Bereich (`privat/`).

Ablauf:
1. IDA ruft `/api/v1/internal/proposals` auf → Wiki prüft Pfad, berechnet das exakte Unified-Diff (`before` vs. `after`),
   speichert das Proposal (`status="pending"`) und gibt `proposal_id`, `diff_preview` und ein einmaliges `commit_secret` zurück.
2. Im Live-Chat zeigt IDA dem Nutzer die Inline-Freigabekarte mit Diff. Klickt er „Erlauben“, sendet IDA
   `POST /api/v1/internal/proposals/{id}/commit` mit `commit_secret`.
3. Bei Hintergrund-Läufen (ohne offenen Live-Chat) bleibt das Proposal als Vorschlag im Wiki stehen und der Nutzer kann es
   in der Wiki-Oberfläche mit einem Klick annehmen oder ablehnen.
"""
import hmac
import secrets
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import Caller, hash_token
from ..config import get_settings
from ..db import pending, utcnow
from ..errors import Forbidden, Invalid, NotFound
from ..models import Page, Proposal
from . import files as file_svc
from . import store
from .vault import compose_markdown, normalize_path, parse_markdown, read_vault_file


async def prepare_proposal(
    db: AsyncSession,
    agent_id: str,
    action: str,
    payload: dict,
) -> dict:
    if action not in ("create", "patch", "update", "move", "delete", "attach"):
        raise Invalid(f"Unbekannte Proposal-Aktion: {action}")

    raw_path = str(payload.get("path") or payload.get("from_path") or "").strip()
    norm_path, space, _, _ = normalize_path(raw_path)
    if space != "privat":
        raise Invalid("Freigabe-Proposals sind nur für den Bereich `privat/` vorgesehen.")

    existing = await db.scalar(select(Page).where(Page.path == norm_path, Page.deleted_at.is_(None)))
    before_md = read_vault_file(existing.path) if existing else ""
    summary = str(payload.get("summary") or payload.get("note") or "").strip()

    if action == "create":
        if existing:
            raise Invalid(f"Seite `{norm_path}` existiert bereits – nutze `wiki_private_patch` zum Ändern.")
        fm = {"title": str(payload.get("title") or norm_path.split("/")[-1]).strip()[:200]}
        if payload.get("tags"):
            fm["tags"] = payload["tags"]
        after_md = compose_markdown(fm, str(payload.get("body") or ""))
        diff_text, _, _ = store.unified_diff("", after_md, f"{norm_path}.md")
        summary = summary or f"Neue Seite `{norm_path}` anlegen"

    elif action in ("patch", "update"):
        if not existing:
            raise NotFound(f"Seite `{norm_path}` existiert nicht")
        fm, body = parse_markdown(before_md)
        if action == "patch":
            body = store.apply_patch_to_body(
                body,
                old_text=payload.get("old_text"),
                new_text=payload.get("new_text"),
                heading=payload.get("heading"),
                mode=str(payload.get("mode") or "append"),
            )
        else:
            body = str(payload.get("body") or "")
        if payload.get("title"):
            fm["title"] = str(payload["title"]).strip()[:200]
        if payload.get("tags") is not None:
            fm["tags"] = payload["tags"]
        after_md = compose_markdown(fm, body)
        diff_text, _, _ = store.unified_diff(before_md, after_md, f"{norm_path}.md")
        summary = summary or f"Seite `{norm_path}` aktualisieren"

    elif action == "move":
        if not existing:
            raise NotFound(f"Seite `{norm_path}` existiert nicht")
        to_norm, to_space, _, _ = normalize_path(str(payload.get("to_path") or ""))
        if to_space != "privat":
            raise Invalid("Verschieben aus `privat/` ist nur innerhalb von `privat/` erlaubt.")
        diff_text = f"--- a/{norm_path}.md\n+++ b/{to_norm}.md\n@@ Verschieben / Umbenennen @@\n-{norm_path}\n+{to_norm}\n"
        summary = summary or f"`{norm_path}` verschieben nach `{to_norm}`"

    elif action == "delete":
        if not existing:
            raise NotFound(f"Seite `{norm_path}` existiert nicht")
        diff_text, _, _ = store.unified_diff(before_md, "", f"{norm_path}.md")
        summary = summary or f"`{norm_path}` in den Papierkorb verschieben"

    else:  # attach
        if not existing:
            raise NotFound(f"Seite `{norm_path}` existiert nicht")
        fname = str(payload.get("filename") or "datei")
        diff_text = f"+++ Anhang zu {norm_path}: {fname} ({payload.get('size', 0)} Bytes)\n"
        summary = summary or f"Datei „{fname}“ an `{norm_path}` anhängen"

    secret = secrets.token_hex(24)
    now = utcnow()
    prop = Proposal(
        agent_id=agent_id,
        action=action,
        target_path=norm_path,
        summary=summary[:240],
        payload={**payload, "path": norm_path},
        diff_preview=diff_text[:20000],
        commit_secret_hash=hash_token(secret),
        status="pending",
        created_at=now,
        expires_at=now + timedelta(minutes=get_settings().proposal_minutes),
    )
    db.add(prop)
    await db.flush()
    pending(db).append({"type": "proposal", "proposal_id": prop.id, "status": "pending", "path": norm_path, "by": agent_id})
    return {
        "proposal": proposal_view(prop),
        "commit_secret": secret,
    }


async def execute_proposal(
    db: AsyncSession,
    prop_id: str,
    *,
    commit_secret: str | None = None,
    ui_caller: Caller | None = None,
    file_bytes: bytes | None = None,
) -> dict:
    prop = await db.get(Proposal, prop_id)
    if not prop:
        raise NotFound("Freigabe-Anfrage nicht gefunden")
    if prop.status != "pending":
        raise Invalid(f"Freigabe-Anfrage wurde bereits bearbeitet ({prop.status})")

    now = utcnow()
    if ui_caller is not None:
        if ui_caller.kind != "user":
            raise Forbidden("Nur der Nutzer kann offene Vorschläge in der Oberfläche freigeben.")
    else:
        if prop.expires_at < now:
            prop.status = "expired"
            raise Invalid("Freigabe-Anfrage ist abgelaufen")
        if not commit_secret or not hmac.compare_digest(hash_token(commit_secret), prop.commit_secret_hash):
            raise Forbidden("Ungültiges Freigabe-Geheimnis")

    agent_caller = Caller(prop.agent_id, "ida")
    p = prop.payload
    if prop.action == "create":
        fm = {}
        if p.get("tags"):
            fm["tags"] = p["tags"]
        res = await store.save_page(
            db,
            agent_caller,
            path=prop.target_path,
            title=p.get("title"),
            body=str(p.get("body") or ""),
            frontmatter=fm,
            note=prop.summary,
            approved_by="user",
            approved_proposal=True,
        )
    elif prop.action == "patch":
        fm_patch = {"tags": p["tags"]} if p.get("tags") is not None else None
        res = await store.patch_page(
            db,
            agent_caller,
            prop.target_path,
            old_text=p.get("old_text"),
            new_text=p.get("new_text"),
            heading=p.get("heading"),
            mode=str(p.get("mode") or "append"),
            frontmatter_patch=fm_patch,
            title=p.get("title"),
            note=prop.summary,
            approved_by="user",
            approved_proposal=True,
        )
    elif prop.action == "update":
        fm = {"tags": p["tags"]} if p.get("tags") is not None else None
        res = await store.save_page(
            db,
            agent_caller,
            path=prop.target_path,
            title=p.get("title"),
            body=str(p.get("body") or ""),
            frontmatter=fm,
            note=prop.summary,
            approved_by="user",
            approved_proposal=True,
        )
    elif prop.action == "move":
        res = await store.move_page(
            db,
            agent_caller,
            prop.target_path,
            str(p.get("to_path") or ""),
            new_title=p.get("title"),
            approved_by="user",
            approved_proposal=True,
        )
    elif prop.action == "delete":
        res = await store.delete_page(
            db,
            agent_caller,
            prop.target_path,
            note=prop.summary,
            approved_by="user",
            approved_proposal=True,
        )
    elif prop.action == "attach":
        raw_bytes = file_bytes
        if raw_bytes is None and p.get("data_b64"):
            import base64
            raw_bytes = base64.b64decode(p["data_b64"])
        if not raw_bytes:
            raise Invalid("Dateiinhalt für Anhang fehlt")
        att = await file_svc.upload_attachment(
            db,
            agent_caller,
            prop.target_path,
            str(p.get("filename") or "datei"),
            str(p.get("mime") or "application/octet-stream"),
            raw_bytes,
            approved_by="user",
            approved_proposal=True,
        )
        res = {"attachment": att}
    else:
        raise Invalid("Unbekannte Aktion")

    prop.status = "committed"
    prop.decided_at = now
    pending(db).append({"type": "proposal", "proposal_id": prop.id, "status": "committed", "path": prop.target_path, "by": prop.agent_id})
    return {"ok": True, "proposal_id": prop.id, **res}


async def reject_proposal(db: AsyncSession, prop_id: str) -> dict:
    prop = await db.get(Proposal, prop_id)
    if not prop:
        raise NotFound("Freigabe-Anfrage nicht gefunden")
    if prop.status == "pending":
        prop.status = "rejected"
        prop.decided_at = utcnow()
        pending(db).append({"type": "proposal", "proposal_id": prop.id, "status": "rejected", "path": prop.target_path, "by": prop.agent_id})
    return {"ok": True, "proposal_id": prop.id, "status": prop.status}


def proposal_view(p: Proposal) -> dict:
    return {
        "id": p.id,
        "agent_id": p.agent_id,
        "action": p.action,
        "target_path": p.target_path,
        "summary": p.summary,
        "diff_preview": p.diff_preview,
        "status": p.status,
        "created_at": p.created_at.isoformat(),
        "expires_at": p.expires_at.isoformat(),
    }
