"""SPEC-08 + casos de lib da SPEC-12 (13, 16–19, 21). HTTP simulado com `responses`."""
from __future__ import annotations

import hashlib
import io
import json
import re
import time
import zipfile
from email.parser import BytesParser
from email.policy import default as default_policy

import pytest
import requests
import responses

import trajetoria_email
from trajetoria_email import Attachment, EmailClient, EmailConfig, EmailSendError, SendResult, SmtpOverride
from trajetoria_email.transport import HttpTransport

API = "https://email-api.test"
URL = f"{API}/v1/emails"
KEY = "chave-de-teste-1234567890"


def ok_body(**kw):
    body = {"email_id": "e1", "status": "sent", "profile": "default", "recipients": {"to": ["a@x.com"], "cc": [], "bcc": []},
            "message_id": "<m@x>", "attempts": 1, "error_code": None, "error_detail": None, "attachments": [],
            "warnings": [], "idempotent_replay": False}
    body.update(kw)
    return body


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    slept = []
    monkeypatch.setattr(HttpTransport, "sleep", staticmethod(lambda s: slept.append(s)))
    return slept


@pytest.fixture
def warnings_seen():
    return []


@pytest.fixture
def mail(warnings_seen):
    return EmailClient(EmailConfig(api_key=KEY, api_base_url=API, on_warning=warnings_seen.append))


def parse_multipart(request):
    """Devolve (payload_dict, [(filename, bytes, content_type)])."""
    ctype = request.headers["Content-Type"]
    raw = b"Content-Type: " + ctype.encode() + b"\r\n\r\n" + request.body
    msg = BytesParser(policy=default_policy).parsebytes(raw)
    payload, files = None, []
    for part in msg.iter_parts():
        name = part.get_param("name", header="content-disposition")
        data = part.get_payload(decode=True)
        if name == "payload":
            payload = json.loads(data)
        else:
            files.append((part.get_filename(), data, part.get_content_type()))
    return payload, files


# ---------------------------------------------------------------- sucesso / falhas
@responses.activate
def test_send_success(mail):
    responses.post(URL, json=ok_body())
    r = mail.send(subject="Relatório — #1", to="a@x.com, b@y.com", cc=["@trajetoria"], html="<p>oi</p>",
                  tags={"tipo": "relatorio", "n": 1}, execution_id=123)
    assert r.ok and bool(r) and r.status == "sent" and r.email_id == "e1" and r.attempts == 1
    req = responses.calls[0].request
    assert req.headers["X-API-Key"] == KEY
    assert req.headers["User-Agent"].startswith("trajetoria-email/")
    assert re.fullmatch(r"[0-9a-f-]{36}", req.headers["Idempotency-Key"])
    payload, files = parse_multipart(req)
    assert payload["to"] == ["a@x.com", "b@y.com"] and payload["cc"] == ["@trajetoria"] and payload["bcc"] == []
    assert payload["mode"] == "prod" and payload["execution_id"] == "123" and payload["tags"] == {"tipo": "relatorio", "n": "1"}
    assert payload["subject"] == "Relatório — #1" and files == []


@responses.activate
def test_send_failed_smtp(mail, warnings_seen):
    responses.post(URL, json=ok_body(status="failed", error_code="SMTP_AUTH_FAILED", error_detail="535"))
    r = mail.send(subject="s", to="a@x.com", text="t")
    assert not r and r.status == "failed" and r.error_code == "SMTP_AUTH_FAILED" and r.attempts == 1
    assert len(responses.calls) == 1                         # não retenta falha SMTP
    assert any("SMTP_AUTH_FAILED" in w for w in warnings_seen)


@pytest.mark.parametrize("status,body,code", [
    (401, {"detail": "Chave de robô inválida.", "error_code": "KEY_INVALID"}, "KEY_INVALID"),
    (403, {"detail": "Chave desativada.", "error_code": "KEY_INACTIVE"}, "KEY_INACTIVE"),
    (403, {"detail": "x", "error_code": "PROFILE_NOT_ALLOWED"}, "PROFILE_NOT_ALLOWED"),
    (400, {"detail": "Grupo '@x' não cadastrado", "error_code": "GROUP_NOT_FOUND"}, "GROUP_NOT_FOUND"),
    (413, {"detail": "grande", "error_code": "ATTACHMENTS_TOO_LARGE"}, "ATTACHMENTS_TOO_LARGE"),
    (409, {"detail": "conflito", "error_code": "IDEMPOTENCY_CONFLICT"}, "IDEMPOTENCY_CONFLICT"),
    (422, {"detail": [{"msg": "x"}]}, "REQUEST_INVALID"),
    (500, None, "API_ERROR"),
])
@responses.activate
def test_request_errors_not_retried(mail, status, body, code):
    if body is None:
        responses.post(URL, status=status, body="Internal Server Error")
    else:
        responses.post(URL, status=status, json=body)
    r = mail.send(subject="s", to="a@x.com", text="t")
    assert not r.ok and r.status == "error" and r.error_code == code and r.http_status == status
    assert len(responses.calls) == 1


@responses.activate
def test_429_respects_retry_after(mail, no_sleep):
    responses.post(URL, status=429, json={"detail": "x", "error_code": "RATE_LIMITED"}, headers={"Retry-After": "7"})
    responses.post(URL, json=ok_body())
    r = mail.send(subject="s", to="a@x.com", text="t")
    assert r.ok and r.attempts == 2 and no_sleep == [7.0]


@responses.activate
def test_429_retry_after_capped(mail, no_sleep):
    responses.post(URL, status=429, json={}, headers={"Retry-After": "3600"})
    responses.post(URL, status=429, json={}, headers={"Retry-After": "3600"})
    responses.post(URL, status=429, json={}, headers={"Retry-After": "3600"})
    r = mail.send(subject="s", to="a@x.com", text="t")
    assert r.error_code == "RATE_LIMITED_API" and r.attempts == 3 and no_sleep == [30.0, 30.0]


@responses.activate
def test_503_retry_same_key_and_same_attachment_bytes(mail, no_sleep, tmp_path):
    """SPEC-12 #21: a retentativa reenvia os MESMOS bytes, mesmo se o arquivo mudar no disco."""
    f = tmp_path / "relatorio.xlsx"
    f.write_bytes(b"PK\x03\x04 versao-1")
    seen = []

    def callback(request):
        seen.append(request)
        if len(seen) == 1:
            f.write_bytes(b"PK\x03\x04 versao-2-alterada")   # arquivo muda entre as tentativas
            return (503, {}, "")
        return (200, {}, json.dumps(ok_body()))

    responses.add_callback(responses.POST, URL, callback=callback)
    r = mail.send(subject="s", to="a@x.com", text="t", attachments=[str(f)])
    assert r.ok and r.attempts == 2 and no_sleep == [2.0]
    k1, k2 = (req.headers["Idempotency-Key"] for req in seen)
    assert k1 == k2
    h = [hashlib.sha256(parse_multipart(req)[1][0][1]).hexdigest() for req in seen]
    assert h[0] == h[1] == hashlib.sha256(b"PK\x03\x04 versao-1").hexdigest()


@responses.activate
def test_each_send_gets_new_idempotency_key(mail):
    responses.post(URL, json=ok_body())
    responses.post(URL, json=ok_body())
    mail.send(subject="s", to="a@x.com", text="t")
    mail.send(subject="s", to="a@x.com", text="t")
    assert responses.calls[0].request.headers["Idempotency-Key"] != responses.calls[1].request.headers["Idempotency-Key"]


@responses.activate
def test_timeout_then_api_unreachable(mail, no_sleep):
    responses.post(URL, body=requests.exceptions.ReadTimeout("read timed out"))
    responses.post(URL, body=requests.exceptions.ConnectTimeout("connect timed out"))
    responses.post(URL, body=requests.exceptions.ConnectionError("refused"))
    r = mail.send(subject="s", to="a@x.com", text="t")
    assert not r.ok and r.status == "error" and r.error_code == "API_UNREACHABLE" and r.attempts == 3
    assert no_sleep == [2.0, 5.0]


def test_api_down_does_not_raise_and_is_bounded(warnings_seen):
    """API fora do ar de verdade (porta fechada): ok=False, API_UNREACHABLE, sem exceção."""
    import socket

    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    cfg = EmailConfig(api_key=KEY, api_base_url=f"http://127.0.0.1:{port}", timeout_seconds=1,
                      connect_timeout_seconds=1, max_attempts=2, on_warning=warnings_seen.append)
    started = time.monotonic()
    r = EmailClient(cfg).send(subject="s", to="a@x.com", text="t")
    assert r.ok is False and r.error_code == "API_UNREACHABLE" and r.attempts == 2
    assert time.monotonic() - started < (1 + 1) * 2 + 2 + 3   # timeout × tentativas + backoff (sleep zerado) + folga


@responses.activate
def test_raise_on_error_and_send_or_raise():
    responses.post(URL, json=ok_body(status="failed", error_code="SMTP_RATE_LIMITED"))
    responses.post(URL, json=ok_body(status="failed", error_code="SMTP_RATE_LIMITED"))
    m = EmailClient(EmailConfig(api_key=KEY, api_base_url=API, raise_on_error=True))
    with pytest.raises(EmailSendError) as e:
        m.send(subject="s", to="a@x.com", text="t")
    assert e.value.result.error_code == "SMTP_RATE_LIMITED"
    m2 = EmailClient(EmailConfig(api_key=KEY, api_base_url=API))
    with pytest.raises(EmailSendError):
        m2.send_or_raise(subject="s", to="a@x.com", text="t")


# ---------------------------------------------------------------- validações locais
@pytest.mark.parametrize("kwargs,code", [
    ({"subject": "s", "to": [], "text": "t"}, "CLIENT_VALIDATION"),
    ({"subject": "s", "to": " , ", "text": "t"}, "CLIENT_VALIDATION"),
    ({"subject": "s", "to": "a@x.com"}, "CLIENT_VALIDATION"),
    ({"subject": "", "to": "a@x.com", "text": "t"}, "CLIENT_VALIDATION"),
    ({"subject": "a\nb", "to": "a@x.com", "text": "t"}, "CLIENT_VALIDATION"),
    ({"subject": "s", "to": "a@x.com", "text": "t", "mode": "qa"}, "CLIENT_VALIDATION"),
    ({"subject": "s", "to": "a@x.com", "text": "t", "attachments": [Attachment("v.exe", b"MZ")]}, "ATTACHMENT_BLOCKED"),
    ({"subject": "s", "to": "a@x.com", "text": "t", "attachments": [Attachment("v.csv", b"")]}, "ATTACHMENT_EMPTY"),
    ({"subject": "s", "to": "a@x.com", "text": "t", "attachments": [Attachment(f"{i}.txt", b"x") for i in range(21)]},
     "TOO_MANY_ATTACHMENTS"),
])
@responses.activate
def test_local_validation_no_http(mail, kwargs, code):
    r = mail.send(**kwargs)
    assert not r.ok and r.error_code == code and r.status == "error"
    assert len(responses.calls) == 0


@responses.activate
def test_dev_mode_allows_no_recipients(mail):
    responses.post(URL, json=ok_body())
    r = mail.send(subject="s", text="t", mode="dev")
    assert r.ok and parse_multipart(responses.calls[0].request)[0]["mode"] == "dev"


@responses.activate
def test_13_attachments_over_18mb_local(mail):
    big = Attachment("grande.bin", b"\0" * (19 * 1024 * 1024))
    r = mail.send(subject="s", to="a@x.com", text="t", attachments=[big])
    assert not r.ok and r.error_code == "ATTACHMENTS_TOO_LARGE" and len(responses.calls) == 0


# ---------------------------------------------------------------- anexos (SPEC-12, lib)
@responses.activate
def test_attachment_forms_and_mime(mail, tmp_path):
    responses.post(URL, json=ok_body())
    xlsx = tmp_path / "relatorio.xlsx"
    xlsx.write_bytes(b"PK\x03\x04xlsx")
    log = tmp_path / "exec.log"
    log.write_bytes(b"linha\r\n")
    csv_bytes = "nome;valor\r\nJoão;1\r\n".encode("utf-8-sig")
    r = mail.send(subject="s", to="a@x.com", text="t", attachments=[
        str(xlsx),
        xlsx,                                                        # Path (nome repetido: servidor renomeia)
        Attachment("dados.csv", csv_bytes),                          # #19 df.to_csv().encode("utf-8-sig")
        Attachment("resumo.txt", "olá".encode("cp1252"), charset="cp1252"),
        Attachment.from_file(open(log, "rb"), filename="execucao.log"),
        io.BytesIO(b"%PDF-1.4"),
        Attachment("x.zip", b"PK\x05\x06" + b"\0" * 18),
        Attachment("m.xlsm", b"PK"),
    ])
    assert r.ok
    payload, files = parse_multipart(responses.calls[0].request)
    assert [f[0] for f in files] == ["relatorio.xlsx", "relatorio.xlsx", "dados.csv", "resumo.txt", "execucao.log",
                                     "anexo", "x.zip", "m.xlsm"]
    assert files[0][2] == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    assert files[2][1] == csv_bytes and files[2][2] == "text/csv"
    assert files[3][2] == "text/plain"
    assert files[4][2] == "text/plain"                                  # .log pela tabela própria
    assert files[6][2] == "application/zip"                             # não application/x-zip-compressed
    assert files[7][2] == "application/vnd.ms-excel.sheet.macroenabled.12"   # o parser do teste normaliza p/ minúsculas
    assert payload["attachments_meta"] == [{"filename": "resumo.txt", "charset": "cp1252"}]


@responses.activate
def test_16_missing_file_non_strict_sends_with_warning(mail, warnings_seen, tmp_path):
    responses.post(URL, json=ok_body())
    ok = tmp_path / "ok.csv"
    ok.write_bytes(b"a;b")
    r = mail.send(subject="s", to="a@x.com", text="t", attachments=[str(tmp_path / "nao_existe.xlsx"), str(ok)])
    assert r.ok
    assert [f[0] for f in parse_multipart(responses.calls[0].request)[1]] == ["ok.csv"]
    assert any("nao_existe.xlsx" in w for w in r.warnings)
    assert any("nao_existe.xlsx" in w for w in warnings_seen)


@responses.activate
def test_17_missing_file_strict_fails_without_http(mail, tmp_path):
    r = mail.send(subject="s", to="a@x.com", text="t", attachments=[str(tmp_path / "nao_existe.xlsx")],
                  strict_attachments=True)
    assert not r.ok and r.error_code == "ATTACHMENT_NOT_FOUND" and len(responses.calls) == 0


@responses.activate
def test_18_compress_auto_large_csv(mail):
    responses.post(URL, json=ok_body())
    line = b"2026-11-25;cliente;valor;123,45;observacao padrao repetida\r\n"
    csv = line * (40 * 1024 * 1024 // len(line))                       # ~40 MB, comprime muito
    r = mail.send(subject="s", to="a@x.com", text="t", attachments=[Attachment("grande.csv", csv)], compress="auto")
    assert r.ok
    files = parse_multipart(responses.calls[0].request)[1]
    assert [f[0] for f in files] == ["anexos.zip"] and files[0][2] == "application/zip"
    assert len(files[0][1]) < 18 * 1024 * 1024
    with zipfile.ZipFile(io.BytesIO(files[0][1])) as zf:
        assert zf.namelist() == ["grande.csv"] and zf.read("grande.csv") == csv
    assert any("compactado" in w for w in r.warnings)


@responses.activate
def test_compress_auto_small_does_nothing_and_always_zips(mail):
    responses.post(URL, json=ok_body())
    responses.post(URL, json=ok_body())
    mail.send(subject="s", to="a@x.com", text="t", attachments=[Attachment("a.csv", b"1")], compress="auto")
    assert [f[0] for f in parse_multipart(responses.calls[0].request)[1]] == ["a.csv"]
    r = mail.send(subject="s", to="a@x.com", text="t", attachments=[Attachment("a.csv", b"1"), Attachment("a.csv", b"2")],
                  compress="always", zip_name="relatorios")
    files = parse_multipart(responses.calls[1].request)[1]
    assert [f[0] for f in files] == ["relatorios.zip"]
    with zipfile.ZipFile(io.BytesIO(files[0][1])) as zf:
        assert zf.namelist() == ["a.csv", "a (2).csv"]
    assert r.ok


@responses.activate
def test_compress_auto_still_too_big(mail):
    import os
    data = os.urandom(19 * 1024 * 1024)                                  # incompressível
    r = mail.send(subject="s", to="a@x.com", text="t", attachments=[Attachment("r.bin", data)], compress="auto")
    assert r.error_code == "ATTACHMENTS_TOO_LARGE" and "mesmo após compactar" in r.error_detail
    assert len(responses.calls) == 0


def test_attachment_type_errors():
    with pytest.raises(TypeError):
        Attachment("a.txt", "texto não é bytes")
    with pytest.raises(TypeError):
        Attachment.from_file(io.StringIO("x"))


# ---------------------------------------------------------------- check / send_error / logger
@responses.activate
def test_check_ok_and_errors(mail, warnings_seen):
    responses.get(f"{API}/v1/keys/me", json={"robot_id": "autopark-rh", "active": True, "groups": ["dev"]})
    ok, info = mail.check()
    assert ok and info["robot_id"] == "autopark-rh" and mail.robot_id == "autopark-rh"
    responses.replace(responses.GET, f"{API}/v1/keys/me", status=403, json={"detail": "Chave desativada."})
    ok, info = mail.check()
    assert not ok and info["error_code"] == "KEY_INACTIVE"
    responses.replace(responses.GET, f"{API}/v1/keys/me", body=requests.exceptions.ConnectionError("x"))
    ok, info = mail.check()
    assert not ok and info["error_code"] == "API_UNREACHABLE"


@responses.activate
def test_send_error_template(mail):
    responses.get(f"{API}/v1/keys/me", json={"robot_id": "graal", "active": True})
    responses.post(URL, json=ok_body())
    tb = 'Traceback:\n  File "x.py", line 1\n<script>alert(1)</script>'
    r = mail.send_error("Falha no login", "O portal recusou a senha.", traceback=tb)
    assert r.ok
    payload, _ = parse_multipart(responses.calls[1].request)
    assert payload["subject"] == "ERRO RPA graal: Falha no login"
    assert payload["to"] == ["@dev"] and payload["tags"] == {"tipo": "erro"}
    assert "&lt;script&gt;" in payload["html"] and "<script>" not in payload["html"]
    assert "<pre" in payload["html"]


class FakeLogger:
    execution_id = "exec-42"

    def __init__(self):
        self.calls = []

    def info(self, msg, **kw):
        self.calls.append(("info", msg, kw))

    def error(self, msg, **kw):
        self.calls.append(("error", msg, kw))


@responses.activate
def test_logger_integration():
    responses.post(URL, json=ok_body())
    responses.post(URL, json=ok_body(status="failed", error_code="SMTP_TEMPORARY"))
    lg = FakeLogger()
    m = EmailClient(EmailConfig(api_key=KEY, api_base_url=API), logger=lg)
    m.send(subject="s", to="a@x.com", text="t")
    m.send(subject="s", to="a@x.com", text="t", execution_id="outro")
    assert parse_multipart(responses.calls[0].request)[0]["execution_id"] == "exec-42"
    assert parse_multipart(responses.calls[1].request)[0]["execution_id"] == "outro"
    assert lg.calls[0][:2] == ("info", "e-mail enviado") and lg.calls[0][2]["email_id"] == "e1"
    assert lg.calls[1][:2] == ("error", "falha no envio de e-mail") and lg.calls[1][2]["error_code"] == "SMTP_TEMPORARY"


@responses.activate
def test_broken_callbacks_never_break_send():
    responses.post(URL, json=ok_body(warnings=["w"]))

    class BadLogger:
        execution_id = None

        def info(self, *a, **k):
            raise RuntimeError("logger quebrado")

    def bad_warning(msg):
        raise RuntimeError("callback quebrado")

    m = EmailClient(EmailConfig(api_key=KEY, api_base_url=API, on_warning=bad_warning), logger=BadLogger())
    assert m.send(subject="s", to="a@x.com", text="t").ok


@responses.activate
def test_smtp_override_and_profile_in_payload():
    responses.post(URL, json=ok_body())
    m = EmailClient(EmailConfig(api_key=KEY, api_base_url=API, default_sender_profile="gadens-o365", mode="hml"))
    m.send(subject="s", to="a@x.com", text="t", reply_to="r@x.com", from_name="Robô",
           smtp_override=SmtpOverride(host="smtp.x.com", port=587, from_address="a@x.com", username="u", password="p"))
    p = parse_multipart(responses.calls[0].request)[0]
    assert p["sender_profile"] == "gadens-o365" and p["mode"] == "hml" and p["reply_to"] == "r@x.com"
    assert p["smtp_override"] == {"host": "smtp.x.com", "port": 587, "from_address": "a@x.com", "username": "u", "password": "p"}


def test_config_validation_and_repr():
    with pytest.raises(ValueError):
        EmailConfig(api_key="")
    with pytest.raises(ValueError):
        EmailConfig(api_key="k", mode="qa")
    c = EmailConfig(api_key=KEY, api_base_url=API + "/")
    assert c.api_base_url == API and KEY not in repr(c)
    assert "segredo" not in repr(SmtpOverride(host="h", port=1, from_address="a", password="segredo"))


def test_readme_path_and_version():
    assert trajetoria_email.README_PATH.exists()
    assert trajetoria_email.__version__ == "0.1.0"
    assert isinstance(SendResult(ok=False, status="error"), SendResult)


@responses.activate
def test_get_status(mail):
    responses.get(f"{API}/v1/emails/e1", json={"email_id": "e1", "status": "sent"})
    responses.get(f"{API}/v1/emails/x", status=404, json={"detail": "nao"})
    assert mail.get_status("e1")["status"] == "sent"
    assert mail.get_status("x") is None
