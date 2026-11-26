"""`EmailClient` — a única classe que o RPA usa.

Princípios: não lê `.env`, não imprime nada (avisos via `on_warning`), nunca derruba o robô
por padrão (`send()` devolve `SendResult` com `ok=False`), não monta MIME nem fala SMTP.
"""
from __future__ import annotations

import re
from typing import Any, Iterable, Optional, Sequence, Union

from .attachments import AttachmentProblem, prepare
from .config import MODES, EmailConfig, SmtpOverride
from .models import EmailSendError, SendResult
from .templates import error_html
from .transport import HttpTransport

Recipients = Union[str, Sequence[str], None]
_SPLIT = re.compile(r"[,;]")


def _recipients(value: Recipients) -> list[str]:
    """Aceita `"a@x, b@y"` (compatível com os .env atuais), lista, ou `@grupo`."""
    if value is None:
        return []
    items: Iterable[str] = _SPLIT.split(value) if isinstance(value, str) else value
    out = []
    for item in items:
        if item is None:
            continue
        for part in _SPLIT.split(str(item)):
            part = part.strip()
            if part:
                out.append(part)
    return out


class EmailClient:
    def __init__(self, config: EmailConfig, logger: Any = None, transport: Optional[HttpTransport] = None):
        self.config = config
        self.logger = logger            # opcional: RPALoggerClient (duck typing: info/error/execution_id)
        self.transport = transport or HttpTransport(config)
        self._robot_id: Optional[str] = None

    # ------------------------------------------------------------------ avisos/log
    def _warn(self, message: str) -> None:
        if self.config.on_warning:
            try:
                self.config.on_warning(message)
            except Exception:  # noqa: BLE001 — callback do usuário nunca derruba o envio
                pass

    def _log_result(self, result: SendResult, subject: str) -> None:
        if self.logger is None:
            return
        try:
            if result.ok:
                self.logger.info("e-mail enviado", email_id=result.email_id, subject=subject,
                                 attachments=len(result.attachments))
            else:
                self.logger.error("falha no envio de e-mail", error_code=result.error_code,
                                  error_detail=result.error_detail, email_id=result.email_id, subject=subject)
        except Exception:  # noqa: BLE001
            pass

    def _finish(self, result: SendResult, subject: str, raise_on_error: Optional[bool]) -> SendResult:
        for w in result.warnings:
            self._warn(w)
        if not result.ok:
            self._warn(f"e-mail não enviado: {result.error_code} {result.error_detail or ''}".strip())
        self._log_result(result, subject)
        should_raise = self.config.raise_on_error if raise_on_error is None else raise_on_error
        if should_raise and not result.ok:
            raise EmailSendError(result)
        return result

    # ------------------------------------------------------------------ envio
    def send(
        self,
        subject: str,
        to: Recipients = None,
        cc: Recipients = None,
        bcc: Recipients = None,
        html: Optional[str] = None,
        text: Optional[str] = None,
        html_only: bool = False,
        attachments: Optional[Iterable[Any]] = None,
        strict_attachments: bool = False,
        compress: str = "never",
        zip_name: str = "anexos.zip",
        reply_to: Optional[str] = None,
        from_name: Optional[str] = None,
        sender_profile: Optional[str] = None,
        smtp_override: Optional[SmtpOverride] = None,
        mode: Optional[str] = None,
        execution_id: Optional[str] = None,
        tags: Optional[dict] = None,
        raise_on_error: Optional[bool] = None,
    ) -> SendResult:
        mode = mode or self.config.mode
        to_l, cc_l, bcc_l = _recipients(to), _recipients(cc), _recipients(bcc)

        def local_error(code: str, detail: str, warnings: Optional[list] = None) -> SendResult:
            return self._finish(
                SendResult(ok=False, status="error", error_code=code, error_detail=detail,
                           warnings=list(warnings or [])),
                subject, raise_on_error,
            )

        # Validações locais: falham rápido, sem upload.
        if mode not in MODES:
            return local_error("CLIENT_VALIDATION", f"mode inválido: {mode!r}")
        if not subject or not str(subject).strip():
            return local_error("CLIENT_VALIDATION", "assunto vazio")
        if "\r" in subject or "\n" in subject:
            return local_error("CLIENT_VALIDATION", "o assunto não pode conter quebras de linha")
        if not (html or text):
            return local_error("CLIENT_VALIDATION", "informe html ou text")
        if mode == "prod" and not (to_l or cc_l or bcc_l):
            return local_error("CLIENT_VALIDATION", "nenhum destinatário (to/cc/bcc)")
        try:
            prepared, att_warnings = prepare(attachments, strict=strict_attachments, compress=compress,
                                             zip_name=zip_name)
        except AttachmentProblem as exc:
            return local_error(exc.code, exc.detail)

        if execution_id is None and self.logger is not None:
            execution_id = getattr(self.logger, "execution_id", None)

        payload: dict[str, Any] = {
            "subject": subject, "to": to_l, "cc": cc_l, "bcc": bcc_l, "mode": mode,
            "html_only": bool(html_only),
        }
        optional = {
            "html": html, "text": text, "reply_to": reply_to, "from_name": from_name,
            "sender_profile": sender_profile or self.config.default_sender_profile,
            "execution_id": str(execution_id) if execution_id is not None else None,
            "tags": {str(k): str(v) for k, v in tags.items()} if tags else None,
            "smtp_override": smtp_override.to_dict() if smtp_override else None,
        }
        payload.update({k: v for k, v in optional.items() if v is not None})
        meta = [{"filename": a.filename, "charset": a.charset} for a in prepared if a.charset]
        if meta:
            payload["attachments_meta"] = meta

        try:
            result = self.transport.send(payload, prepared)
        except Exception as exc:  # noqa: BLE001 — a lib nunca derruba o robô
            result = SendResult(ok=False, status="error", error_code="CLIENT_ERROR",
                                error_detail=f"{type(exc).__name__}: {exc}")
        if att_warnings:
            result = _with_warnings(result, att_warnings)
        return self._finish(result, subject, raise_on_error)

    def send_or_raise(self, *args: Any, **kwargs: Any) -> SendResult:
        kwargs["raise_on_error"] = True
        return self.send(*args, **kwargs)

    def send_error(
        self,
        title: str,
        message: str,
        traceback: Optional[str] = None,
        attachments: Optional[Iterable[Any]] = None,
        to: Recipients = ("@dev",),
        **kwargs: Any,
    ) -> SendResult:
        """Alerta de erro padronizado: assunto `ERRO RPA <robot_id>: <title>`, traceback
        escapado em `<pre>`. Por padrão vai só para `@dev` (traceback não vai ao cliente)."""
        robot_id = self.robot_id or "RPA"
        execution_id = kwargs.get("execution_id")
        if execution_id is None and self.logger is not None:
            execution_id = getattr(self.logger, "execution_id", None)
        title_line = " ".join(str(title).split())
        html = error_html(title_line, message, robot_id, traceback, execution_id)
        tags = {"tipo": "erro", **(kwargs.pop("tags", None) or {})}
        return self.send(subject=f"ERRO RPA {robot_id}: {title_line}"[:900], to=to, html=html,
                         attachments=attachments, tags=tags, **kwargs)

    # ------------------------------------------------------------------ consultas
    def check(self) -> tuple[bool, dict]:
        """`GET /v1/keys/me` — valida chave/URL no início da execução. Nunca levanta.

        Devolve `(True, {robot_id, active, default_profile, allowed_profiles, groups})` ou
        `(False, {error_code, error_detail, http_status})`.
        """
        try:
            status, body, net_error = self.transport.get_json("/v1/keys/me")
        except Exception as exc:  # noqa: BLE001
            status, body, net_error = None, None, f"{type(exc).__name__}: {exc}"
        if net_error is not None:
            info = {"error_code": "API_UNREACHABLE", "error_detail": net_error, "http_status": None}
        elif status == 200 and isinstance(body, dict):
            self._robot_id = body.get("robot_id")
            return True, body
        else:
            code = {401: "KEY_INVALID", 403: "KEY_INACTIVE"}.get(status or 0, "API_ERROR")
            detail = body.get("detail") if isinstance(body, dict) else None
            info = {"error_code": code, "error_detail": detail or f"HTTP {status}", "http_status": status}
        self._warn(f"verificação da API de e-mail falhou: {info['error_code']} {info['error_detail']}")
        return False, info

    @property
    def robot_id(self) -> Optional[str]:
        if self._robot_id is None:
            self.check()
        return self._robot_id

    def get_status(self, email_id: str) -> Optional[dict]:
        """`GET /v1/emails/{id}` — resultado de um envio anterior deste robô (ou None)."""
        try:
            status, body, net_error = self.transport.get_json(f"/v1/emails/{email_id}")
        except Exception:  # noqa: BLE001
            return None
        return body if status == 200 and isinstance(body, dict) else None


def _with_warnings(result: SendResult, extra: list) -> SendResult:
    from dataclasses import replace

    return replace(result, warnings=list(extra) + list(result.warnings))
