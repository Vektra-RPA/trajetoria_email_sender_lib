"""trajetoria_email — cliente da API de e-mail dos RPAs da Trajetória/Vektra.

Uso mínimo:

    from trajetoria_email import EmailClient, EmailConfig

    mail = EmailClient(EmailConfig(api_key=os.environ["EMAIL_API_KEY"],
                                   api_base_url=os.environ["EMAIL_API_URL"]))
    result = mail.send(subject="Relatório", to=["@cliente"], cc=["@trajetoria"],
                       html=html, attachments=[r"C:\\RPA\\x\\relatorio.xlsx"])
    if not result.ok:
        log.error("e-mail não enviado: %s %s", result.error_code, result.error_detail)

Documentação completa: o README.md é instalado JUNTO com o pacote
(`trajetoria_email.README_PATH`):

    python -c "import trajetoria_email; print(trajetoria_email.README_PATH)"
"""
from pathlib import Path

from ._version import __version__
from .client import EmailClient
from .config import EmailConfig, SmtpOverride
from .models import Attachment, EmailSendError, SendResult

__all__ = [
    "EmailClient",
    "EmailConfig",
    "SmtpOverride",
    "Attachment",
    "SendResult",
    "EmailSendError",
    "README_PATH",
    "__version__",
]

# Copiado para dentro do pacote no build (ver setup.py). Num clone ou instalação editável o
# caminho cai no original, na raiz do repositório.
_PKG_DIR = Path(__file__).resolve().parent
README_PATH = (
    _PKG_DIR / "README.md" if (_PKG_DIR / "README.md").exists() else _PKG_DIR.parent / "README.md"
)
