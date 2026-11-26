"""Normalização e validação local dos anexos (antes de qualquer rede).

Aceita `str`/`Path` (lido em binário UMA vez), `Attachment` (bytes em memória) e objetos de
arquivo binário. Os bytes lidos aqui são os mesmos reenviados nas retentativas.
"""
from __future__ import annotations

import io
import os
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Optional

from ._mime import BLOCKED_EXTENSIONS, extension, guess_content_type
from .models import Attachment

MAX_ATTACHMENTS = 20
MAX_ATTACHMENTS_BYTES = 18 * 1024 * 1024


class AttachmentProblem(Exception):
    def __init__(self, code: str, detail: str):
        super().__init__(detail)
        self.code = code
        self.detail = detail


@dataclass
class PreparedAttachment:
    filename: str
    content: bytes
    content_type: str
    charset: Optional[str]

    @property
    def size(self) -> int:
        return len(self.content)


def _to_attachment(item: Any) -> Attachment:
    if isinstance(item, Attachment):
        return item
    if isinstance(item, (str, Path)):
        path = Path(item)
        with open(path, "rb") as fh:
            return Attachment(filename=path.name, content=fh.read())
    if hasattr(item, "read"):
        return Attachment.from_file(item)
    raise TypeError(f"anexo de tipo não suportado: {type(item).__name__}")


def _zip(items: list[PreparedAttachment]) -> bytes:
    buf = io.BytesIO()
    used: set[str] = set()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for a in items:
            name, n = a.filename, 1
            stem, ext = os.path.splitext(a.filename)
            while name.lower() in used:
                n += 1
                name = f"{stem} ({n}){ext}"
            used.add(name.lower())
            zf.writestr(name, a.content)
    return buf.getvalue()


def prepare(
    items: Optional[Iterable[Any]],
    *,
    strict: bool = False,
    compress: str = "never",
    zip_name: str = "anexos.zip",
    max_count: int = MAX_ATTACHMENTS,
    max_bytes: int = MAX_ATTACHMENTS_BYTES,
    blocked: frozenset = BLOCKED_EXTENSIONS,
) -> tuple[list[PreparedAttachment], list[str]]:
    """Devolve (anexos prontos, warnings). Levanta `AttachmentProblem` nas recusas locais."""
    if compress not in ("never", "always", "auto"):
        raise ValueError("compress deve ser 'never', 'always' ou 'auto'")
    warnings: list[str] = []
    prepared: list[PreparedAttachment] = []
    for item in items or []:
        try:
            att = _to_attachment(item)
        except (FileNotFoundError, IsADirectoryError, PermissionError, OSError) as exc:
            label = str(item)
            if strict:
                raise AttachmentProblem("ATTACHMENT_NOT_FOUND", f"anexo inexistente ou ilegível: {label} ({exc.__class__.__name__})")
            warnings.append(f"anexo ignorado (inexistente ou ilegível): {label}")
            continue
        name = os.path.basename(att.filename.replace("\\", "/")) or "anexo"
        if extension(name) in blocked:
            raise AttachmentProblem("ATTACHMENT_BLOCKED", f"tipo de arquivo bloqueado: {name}")
        if att.size == 0:
            raise AttachmentProblem("ATTACHMENT_EMPTY", f"anexo vazio (0 bytes): {name}")
        prepared.append(PreparedAttachment(
            filename=name,
            content=att.content,
            content_type=att.content_type or guess_content_type(name),
            charset=att.charset,
        ))

    total = sum(a.size for a in prepared)
    if prepared and (compress == "always" or (compress == "auto" and total > max_bytes)):
        data = _zip(prepared)
        if extension(zip_name) != ".zip":
            zip_name += ".zip"
        warnings.append(
            f"{len(prepared)} anexo(s) compactado(s) em {zip_name} ({total} → {len(data)} bytes)"
        )
        prepared = [PreparedAttachment(zip_name, data, "application/zip", None)]
        total = len(data)

    if len(prepared) > max_count:
        raise AttachmentProblem("TOO_MANY_ATTACHMENTS", f"{len(prepared)} anexos; o máximo é {max_count}")
    if total > max_bytes:
        raise AttachmentProblem(
            "ATTACHMENTS_TOO_LARGE",
            f"anexos somam {total} bytes; o máximo é {max_bytes} bytes"
            + (" (mesmo após compactar)" if compress != "never" else " (tente compress='auto')"),
        )
    return prepared, warnings
