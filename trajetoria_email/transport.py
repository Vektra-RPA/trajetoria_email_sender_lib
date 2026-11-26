"""HTTP para a API: multipart, retentativas e idempotência.

Retenta SÓ o que é seguro: erro de rede/timeout, 429 (respeitando `Retry-After`) e
502/503/504. Toda retentativa reusa a MESMA `Idempotency-Key` e os MESMOS bytes de anexo,
então um timeout de leitura ambíguo (o servidor pode ter enviado) não duplica o e-mail.
"""
from __future__ import annotations

import json
import platform
import time
import uuid
from typing import Any, Callable, Optional

import requests

from ._version import __version__
from .attachments import PreparedAttachment
from .config import EmailConfig
from .models import SendResult

RETRY_STATUS = {502, 503, 504}


def _user_agent() -> str:
    return f"trajetoria-email/{__version__} python/{platform.python_version()}"


class HttpTransport:
    sleep: Callable[[float], None] = staticmethod(time.sleep)

    def __init__(self, config: EmailConfig, session: Optional[requests.Session] = None):
        self.config = config
        self.session = session or requests.Session()
        self.session.headers.update({"X-API-Key": config.api_key, "User-Agent": _user_agent()})

    # ------------------------------------------------------------------ util
    def _timeout(self) -> tuple[float, float]:
        return (self.config.connect_timeout_seconds, self.config.timeout_seconds)

    def _backoff(self, attempt: int) -> float:
        seq = self.config.backoff_seconds or (2.0,)
        return float(seq[min(attempt - 1, len(seq) - 1)])

    @staticmethod
    def _json(resp: requests.Response) -> Any:
        try:
            return resp.json()
        except ValueError:
            return None

    def _retry_after(self, resp: requests.Response, attempt: int) -> float:
        raw = resp.headers.get("Retry-After")
        try:
            wait = float(raw) if raw is not None else self._backoff(attempt)
        except ValueError:
            wait = self._backoff(attempt)
        return max(0.0, min(wait, self.config.max_retry_after_seconds))

    # ------------------------------------------------------------------ envio
    def send(self, payload: dict, attachments: list[PreparedAttachment]) -> SendResult:
        idem_key = str(uuid.uuid4())   # um por send(), reusado em todas as tentativas
        url = f"{self.config.api_base_url}/v1/emails"
        payload_json = json.dumps(payload, ensure_ascii=False)
        last_error = ("API_UNREACHABLE", "sem resposta da API", None)
        attempt = 0
        while attempt < self.config.max_attempts:
            attempt += 1
            files = [("payload", (None, payload_json.encode("utf-8"), "application/json"))]
            files += [("attachments", (a.filename, a.content, a.content_type)) for a in attachments]
            wait: Optional[float] = None
            try:
                resp = self.session.post(
                    url, files=files, headers={"Idempotency-Key": idem_key},
                    timeout=self._timeout(), verify=self.config.verify_tls,
                )
            except requests.exceptions.SSLError as exc:
                # Certificado inválido não melhora com retentativa.
                return SendResult(ok=False, status="error", error_code="API_UNREACHABLE",
                                  error_detail=f"falha TLS: {exc}", attempts=attempt)
            except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as exc:
                last_error = ("API_UNREACHABLE", f"{type(exc).__name__}: {exc}", None)
            else:
                status = resp.status_code
                body = self._json(resp)
                if status == 200 and isinstance(body, dict):
                    return self._from_body(body, attempt, status)
                if status == 429:
                    last_error = ("RATE_LIMITED_API", self._detail(body, resp), status)
                    wait = self._retry_after(resp, attempt)
                elif status in RETRY_STATUS:
                    last_error = ("API_UNREACHABLE", f"HTTP {status}", status)
                else:
                    return self._request_error(status, body, resp, attempt)
            if attempt >= self.config.max_attempts:
                break
            self.sleep(wait if wait is not None else self._backoff(attempt))
        code, detail, status = last_error
        return SendResult(ok=False, status="error", error_code=code, error_detail=detail,
                          attempts=attempt, http_status=status)

    @staticmethod
    def _detail(body: Any, resp: requests.Response) -> str:
        if isinstance(body, dict) and "detail" in body:
            d = body["detail"]
            return d if isinstance(d, str) else json.dumps(d, ensure_ascii=False)
        return (resp.text or f"HTTP {resp.status_code}")[:500]

    def _request_error(self, status: int, body: Any, resp: requests.Response, attempt: int) -> SendResult:
        server_code = body.get("error_code") if isinstance(body, dict) else None
        if status == 401:
            code = "KEY_INVALID"
        elif status == 403 and server_code in (None, "KEY_INACTIVE"):
            code = "KEY_INACTIVE"
        elif server_code:
            code = server_code             # ex.: GROUP_NOT_FOUND, ATTACHMENT_BLOCKED, PROFILE_NOT_ALLOWED
        elif 400 <= status < 500:
            code = "REQUEST_INVALID"
        else:
            code = "API_ERROR"
        return SendResult(ok=False, status="error", error_code=code, error_detail=self._detail(body, resp),
                          attempts=attempt, http_status=status)

    @staticmethod
    def _from_body(body: dict, attempt: int, status: int) -> SendResult:
        return SendResult(
            ok=body.get("status") == "sent",
            status=body.get("status", "failed"),
            email_id=body.get("email_id"),
            error_code=body.get("error_code"),
            error_detail=body.get("error_detail"),
            recipients=body.get("recipients"),
            attachments=list(body.get("attachments") or []),
            message_id=body.get("message_id"),
            warnings=list(body.get("warnings") or []),
            attempts=attempt,
            http_status=status,
            idempotent_replay=bool(body.get("idempotent_replay")),
        )

    # ------------------------------------------------------------------ consultas
    def get_json(self, path: str) -> tuple[Optional[int], Any, Optional[str]]:
        """GET simples (1 tentativa). Devolve (status, corpo, erro_de_rede)."""
        try:
            resp = self.session.get(f"{self.config.api_base_url}{path}", timeout=self._timeout(),
                                    verify=self.config.verify_tls)
        except requests.exceptions.RequestException as exc:
            return None, None, f"{type(exc).__name__}: {exc}"
        return resp.status_code, self._json(resp), None
