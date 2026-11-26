"""Configuração explícita — a lib NÃO lê `.env` (quem lê é a aplicação)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional

DEFAULT_API_URL = "https://email-api.trajetoriaconsultoria.com"
MODES = ("prod", "hml", "dev")


@dataclass
class SmtpOverride:
    """Credencial SMTP avulsa — só funciona se a chave tiver `allow_custom_smtp`.
    Uso desencorajado: o caminho certo é cadastrar um perfil no servidor."""

    host: str
    port: int
    from_address: str
    username: Optional[str] = None
    password: Optional[str] = field(default=None, repr=False)
    security: Optional[str] = None        # "ssl" | "starttls" | "none"; None → pela porta
    from_name: Optional[str] = None

    def to_dict(self) -> dict:
        d = {"host": self.host, "port": self.port, "from_address": self.from_address}
        for k in ("username", "password", "security", "from_name"):
            v = getattr(self, k)
            if v is not None:
                d[k] = v
        return d


@dataclass
class EmailConfig:
    api_key: str
    api_base_url: str = DEFAULT_API_URL
    mode: str = "prod"                              # "prod" | "hml" | "dev"
    default_sender_profile: Optional[str] = None
    timeout_seconds: float = 60.0                   # timeout HTTP por tentativa (leitura)
    connect_timeout_seconds: float = 10.0
    max_attempts: int = 3                           # rede / 429 / 502-504
    backoff_seconds: tuple = (2.0, 5.0, 10.0)
    max_retry_after_seconds: float = 30.0
    verify_tls: bool = True
    raise_on_error: bool = False
    on_warning: Optional[Callable[[str], None]] = None

    def __post_init__(self) -> None:
        if not self.api_key:
            raise ValueError("EmailConfig.api_key é obrigatório")
        if self.mode not in MODES:
            raise ValueError(f"mode deve ser um de {MODES}")
        self.api_base_url = (self.api_base_url or DEFAULT_API_URL).rstrip("/")
        self.max_attempts = max(1, int(self.max_attempts))

    def __repr__(self) -> str:  # nunca mostra a chave inteira
        k = self.api_key
        masked = f"{k[:4]}…{k[-4:]}" if len(k) > 10 else "…"
        return f"EmailConfig(api_key='{masked}', api_base_url='{self.api_base_url}', mode='{self.mode}')"
