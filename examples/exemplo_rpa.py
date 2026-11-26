"""Exemplo de integração num RPA (a APLICAÇÃO lê o .env; a lib não).

    pip install python-dotenv
    python examples/exemplo_rpa.py
"""
from __future__ import annotations

import logging
import os
import traceback

from trajetoria_email import Attachment, EmailClient, EmailConfig

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("rpa")

mail = EmailClient(EmailConfig(
    api_key=os.environ["EMAIL_API_KEY"],
    api_base_url=os.environ.get("EMAIL_API_URL", "https://email-sender.trajetoriaconsultoria.com"),
    mode={"PROD": "prod", "HML": "hml"}.get(os.getenv("AMBIENTE", "DEV"), "dev"),
    on_warning=lambda m: log.warning("[email] %s", m),
))


def main() -> None:
    ok, info = mail.check()
    if not ok:
        log.error("API de e-mail indisponível: %s", info)   # o robô segue mesmo assim

    html = "<h2>Relatório de teste</h2><p>Execução concluída com <b>sucesso</b>.</p>"
    csv = "nome;valor\r\nJoão;1,50\r\n".encode("utf-8-sig")
    result = mail.send(
        subject="Relatório RPA Exemplo — Execução #1",
        to=["@cliente"],
        cc=["@trajetoria"],
        html=html,
        attachments=[Attachment("detalhe.csv", csv)],
        tags={"tipo": "relatorio"},
    )
    log.info("resultado: ok=%s status=%s code=%s destinatarios=%s", result.ok, result.status,
             result.error_code, result.recipients)

    try:
        1 / 0
    except ZeroDivisionError:
        mail.send_error("Erro inesperado", "Falha ao processar a planilha.", traceback=traceback.format_exc())


if __name__ == "__main__":
    main()
