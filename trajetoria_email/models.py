"""Tipos públicos: `Attachment`, `SendResult`, `EmailSendError`."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, BinaryIO, Optional


@dataclass
class Attachment:
    """Anexo em memória.

        Attachment("dados.csv", df.to_csv(sep=";", index=False).encode("utf-8-sig"))
        Attachment("resumo.txt", texto.encode("cp1252"), charset="cp1252")

    `content_type=None` → a lib deduz pela extensão (mesma tabela do servidor).
    """

    filename: str
    content: bytes
    content_type: Optional[str] = None
    charset: Optional[str] = None

    def __post_init__(self) -> None:
        if isinstance(self.content, (bytearray, memoryview)):
            self.content = bytes(self.content)
        if not isinstance(self.content, bytes):
            raise TypeError(
                "Attachment.content deve ser bytes (ex.: texto.encode('utf-8')); "
                f"recebido {type(self.content).__name__}"
            )

    @classmethod
    def from_file(cls, fileobj: BinaryIO, filename: Optional[str] = None, **kwargs: Any) -> "Attachment":
        """Lê um arquivo já aberto em modo binário (`open(..., "rb")`, `io.BytesIO`…)."""
        data = fileobj.read()
        if isinstance(data, str):
            raise TypeError("abra o arquivo em modo binário ('rb')")
        name = filename or os.path.basename(getattr(fileobj, "name", "") or "") or "anexo"
        return cls(filename=name, content=data, **kwargs)

    @property
    def size(self) -> int:
        return len(self.content)


@dataclass(frozen=True)
class SendResult:
    ok: bool                                  # status == "sent"
    status: str                               # "sent" | "failed" | "error" (sem resposta da API)
    email_id: Optional[str] = None
    error_code: Optional[str] = None
    error_detail: Optional[str] = None
    recipients: Optional[dict] = None         # destinatários efetivos (após grupos e modo)
    attachments: list = field(default_factory=list)
    message_id: Optional[str] = None
    warnings: list = field(default_factory=list)
    attempts: int = 0                         # tentativas HTTP da lib
    http_status: Optional[int] = None
    idempotent_replay: bool = False

    def __bool__(self) -> bool:
        return self.ok


class EmailSendError(Exception):
    """Levantada por `send_or_raise()` ou com `EmailConfig(raise_on_error=True)`."""

    def __init__(self, result: SendResult):
        self.result = result
        super().__init__(f"{result.error_code}: {result.error_detail}")
