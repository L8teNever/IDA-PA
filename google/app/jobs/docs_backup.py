"""Docs-Backup nach Google Drive: holt die ZIP-Sicherung aus IDA Docs und legt sie in einem Drive-Ordner ab.

Scope drive.file: der Dienst sieht nur Dateien, die er selbst angelegt hat -- genau richtig fuer ein Backup. Die aeltesten
Sicherungen werden nach `keep` Stueck geloescht.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

import requests

from app.google_client import GoogleApiClient, GoogleApiError

log = logging.getLogger("ida-google.backup")
SCOPES = ["https://www.googleapis.com/auth/drive.file"]
DRIVE = "https://www.googleapis.com/drive/v3"
UPLOAD = "https://www.googleapis.com/upload/drive/v3/files"
FOLDER_MIME = "application/vnd.google-apps.folder"
PREFIX = "ida-docs-backup-"


def _q(s: str) -> str:
    return s.replace("\\", "\\\\").replace("'", "\\'")


class Drive:
    def __init__(self, client: GoogleApiClient) -> None:
        self.c = client

    def find_folder(self, name: str) -> str | None:
        d = self.c.request("GET", f"{DRIVE}/files", scopes=SCOPES, params={
            "q": f"name = '{_q(name)}' and mimeType = '{FOLDER_MIME}' and trashed = false", "fields": "files(id)", "pageSize": 1})
        files = (d or {}).get("files", [])
        return files[0]["id"] if files else None

    def make_folder(self, name: str) -> str:
        return self.c.request("POST", f"{DRIVE}/files", scopes=SCOPES, json_body={"name": name, "mimeType": FOLDER_MIME})["id"]

    def upload(self, name: str, data: bytes, folder_id: str, mime: str = "application/zip") -> dict:
        boundary = "ida-google-bkp"
        head = (f"--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n"
                f"{json.dumps({'name': name, 'parents': [folder_id]})}\r\n--{boundary}\r\nContent-Type: {mime}\r\n\r\n").encode()
        body = head + data + f"\r\n--{boundary}--".encode()
        token = self.c._google.get_access_token(SCOPES)  # noqa: SLF001 – gleicher Weg wie drive.py
        r = requests.post(f"{UPLOAD}?uploadType=multipart&fields=id,name,size", data=body, timeout=300,
                          headers={"Authorization": f"Bearer {token}", "Content-Type": f"multipart/related; boundary={boundary}"})
        if r.status_code >= 300:
            raise GoogleApiError(f"Drive-Upload fehlgeschlagen: {r.text[:200]}")
        return r.json()

    def backups(self, folder_id: str) -> list[dict]:
        d = self.c.request("GET", f"{DRIVE}/files", scopes=SCOPES, params={
            "q": f"'{folder_id}' in parents and trashed = false and name contains '{PREFIX}'",
            "fields": "files(id,name,createdTime)", "orderBy": "createdTime desc", "pageSize": 200})
        return (d or {}).get("files", [])

    def delete(self, file_id: str) -> None:
        self.c.request("DELETE", f"{DRIVE}/files/{file_id}", scopes=SCOPES)


def run_backup(drive: Drive, docs_url: str, folder: str, keep: int, now: datetime | None = None) -> dict:
    r = requests.get(f"{docs_url}/api/v1/backup", timeout=300)
    if r.status_code >= 300 or not r.content:
        raise GoogleApiError(f"IDA Docs liefert keine Sicherung (HTTP {r.status_code})")
    folder_id = drive.find_folder(folder) or drive.make_folder(folder)
    stamp = (now or datetime.now(timezone.utc)).strftime("%Y-%m-%d_%H%M")
    up = drive.upload(f"{PREFIX}{stamp}.zip", r.content, folder_id)
    removed = 0
    for old in drive.backups(folder_id)[keep:]:
        try:
            drive.delete(old["id"])
            removed += 1
        except GoogleApiError as exc:
            log.warning("Alte Sicherung nicht geloescht: %s", exc)
    return {"datei": up.get("name"), "id": up.get("id"), "bytes": len(r.content), "ordner": folder, "alte_geloescht": removed}
