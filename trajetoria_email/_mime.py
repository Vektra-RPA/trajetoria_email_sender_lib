"""Tabela MIME e extensões bloqueadas — CÓPIA da do servidor (app/mail/attachments.py).

A lib não depende do servidor; manter as duas tabelas idênticas. Não usa `mimetypes` antes
da tabela porque no Windows ele lê o registro (`.zip` vira `application/x-zip-compressed`,
`.xlsm`/`.log` ficam sem tipo).
"""
from __future__ import annotations

import mimetypes
import os

MIME_TABLE: dict[str, str] = {
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".xlsm": "application/vnd.ms-excel.sheet.macroEnabled.12",
    ".xls": "application/vnd.ms-excel",
    ".csv": "text/csv",
    ".txt": "text/plain",
    ".log": "text/plain",
    ".json": "application/json",
    ".xml": "application/xml",
    ".pdf": "application/pdf",
    ".zip": "application/zip",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".html": "text/html",
    ".htm": "text/html",
}

BLOCKED_EXTENSIONS = frozenset(
    ".exe .bat .cmd .com .js .jse .vbs .vbe .ps1 .scr .msi .msp .jar .cpl .hta .lnk .wsf .reg".split()
)


def extension(filename: str) -> str:
    return os.path.splitext(filename)[1].lower()


def guess_content_type(filename: str) -> str:
    ext = extension(filename)
    if ext in MIME_TABLE:
        return MIME_TABLE[ext]
    guessed, _ = mimetypes.guess_type("x" + ext) if ext else (None, None)
    return guessed or "application/octet-stream"
