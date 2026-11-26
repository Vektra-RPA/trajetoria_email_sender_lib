# trajetoria-email

Cliente Python da **API de e-mail dos RPAs da Trajetória** (`email_sender`). O robô não fala
mais SMTP nem guarda senha de e-mail: ele chama a API, que envia pelo remetente cadastrado no
servidor e devolve o resultado.

- Python **≥ 3.10** · única dependência: `requests`
- **Nunca derruba o robô** por padrão: `send()` devolve `SendResult` com `ok=False`
- **Não lê `.env`** e **não imprime nada** (avisos via callback `on_warning`)
- Retentativa segura (rede, 429, 502–504) com `Idempotency-Key` → sem e-mail duplicado
- Anexos de qualquer tipo comum (xlsx, csv, txt, pdf, zip, png…), por caminho, bytes ou arquivo aberto

## Instalação

```bash
pip install "git+https://github.com/Vektra-RPA/trajetoria_email_sender_lib.git@v0.1.0"
```

`requirements.txt`:

```
trajetoria-email @ git+https://github.com/Vektra-RPA/trajetoria_email_sender_lib.git@v0.1.0
```

VM sem git/GitHub: instale o wheel anexado à release (`pip install trajetoria_email-0.1.0-py3-none-any.whl`).

Este README vai junto com o pacote instalado:

```bash
python -c "import trajetoria_email; print(trajetoria_email.README_PATH)"
```

## Configuração no robô (`.env`)

```dotenv
EMAIL_API_KEY=<chave do robô, gerada pelo admin>
EMAIL_API_URL=https://email-api.trajetoriaconsultoria.com
```

Remetente, senha e listas de destinatários **não** ficam mais no robô: ficam no servidor
(perfil de remetente e grupos `@cliente`, `@trajetoria`, `@dev` do robô).

## Uso

```python
import logging
import os

from trajetoria_email import Attachment, EmailClient, EmailConfig

log = logging.getLogger(__name__)

mail = EmailClient(EmailConfig(
    api_key=os.environ["EMAIL_API_KEY"],
    api_base_url=os.environ.get("EMAIL_API_URL", "https://email-api.trajetoriaconsultoria.com"),
    mode={"PROD": "prod", "HML": "hml"}.get(os.getenv("AMBIENTE", "PROD"), "dev"),
    on_warning=lambda m: log.warning("[email] %s", m),
))

ok, info = mail.check()          # opcional, no início: valida chave e URL (nunca levanta)

result = mail.send(
    subject="Relatório RPA AutoPark RH (ADMISSION) — Execução #123",
    to=["@cliente"],             # grupos do servidor e/ou endereços; aceita "a@x, b@y"
    cc=["@trajetoria"],
    html=html,                   # e/ou text=...
    attachments=[r"C:\RPA\Projeto\data\relatorio.xlsx"],
    execution_id=execution_id,   # correlaciona com a API de Logs
    tags={"tipo": "relatorio"},
)
if not result.ok:
    log.error("e-mail não enviado: %s %s", result.error_code, result.error_detail)
```

`bool(result)` é `result.ok`, então `if mail.send(...):` funciona como os `bool` antigos.

### `EmailConfig`

| Campo | Default | Descrição |
|---|---|---|
| `api_key` | — | chave do robô |
| `api_base_url` | `https://email-api.trajetoriaconsultoria.com` | URL da API |
| `mode` | `"prod"` | `prod` \| `hml` \| `dev`. Em `hml`/`dev` o **servidor** troca todos os destinatários pelo grupo `@dev` e prefixa o assunto com `[HML] `/`[DEV] ` |
| `default_sender_profile` | `None` | perfil de remetente deste robô (senão o servidor decide) |
| `timeout_seconds` | `60` | timeout de leitura por tentativa |
| `connect_timeout_seconds` | `10` | timeout de conexão |
| `max_attempts` | `3` | tentativas para rede / 429 / 502–504 (backoff 2 s, 5 s; respeita `Retry-After` até 30 s) |
| `verify_tls` | `True` | |
| `raise_on_error` | `False` | levanta `EmailSendError` em vez de devolver `ok=False` |
| `on_warning` | `None` | callback de avisos (anexo ignorado, recusa parcial, limite do provedor…) |

### `send(...)`

| Parâmetro | Observação |
|---|---|
| `subject` | obrigatório, sem quebra de linha |
| `to`, `cc`, `bcc` | lista ou `"a@x, b@y"`; itens `@grupo` são resolvidos no servidor |
| `html`, `text` | ao menos um. Só `html` → o servidor gera a alternativa texto |
| `html_only` | `True` = envia o HTML exatamente como veio, sem alternativa texto (caso Graal) |
| `attachments` | lista de `str`/`Path` (lido em binário), `Attachment` (bytes) ou arquivo aberto em `"rb"` |
| `strict_attachments` | `False` (default): arquivo inexistente é ignorado com aviso. `True`: `ok=False`, `ATTACHMENT_NOT_FOUND`, nada é enviado |
| `compress` | `"never"` (default) \| `"always"` \| `"auto"` (zipa só se passar de 18 MB) |
| `zip_name` | `"anexos.zip"` |
| `reply_to`, `from_name` | o endereço do remetente vem do perfil |
| `sender_profile` | perfil liberado para a chave |
| `smtp_override` | `SmtpOverride(...)` — só se a chave permitir (desencorajado) |
| `mode` | sobrescreve o do config |
| `execution_id`, `tags` | auditoria (`tags`: até 10 pares) |
| `raise_on_error` | sobrescreve o do config |

### Anexos

```python
from pathlib import Path
from trajetoria_email import Attachment

attachments = [
    r"C:\RPA\Projeto\data\relatorio.xlsx",                                          # caminho
    Path("saida/detalhe.csv"),
    Attachment("dados.csv", df.to_csv(sep=";", index=False).encode("utf-8-sig")),  # memória (abre certo no Excel)
    Attachment("resumo.txt", texto.encode("cp1252"), charset="cp1252"),            # charset explícito
    Attachment.from_file(open("execucao.log", "rb")),                               # arquivo aberto
]
```

- O tipo MIME vem de uma tabela própria (igual à do servidor) — não depende do registro do Windows.
- Os bytes são lidos **uma vez** e reaproveitados nas retentativas; nada é alterado no caminho
  (BOM, `;`, `cp1252`, CRLF chegam intactos).
- Limites: 20 arquivos, 18 MB somados (checados **antes** do upload), arquivo vazio recusado,
  executáveis bloqueados (`.exe .bat .cmd .js .vbs .ps1 .msi .jar .lnk .reg …`, inclusive dentro de `.zip`).

### Atalhos

```python
mail.send_error("Falha no login", "O portal recusou a senha.", traceback=traceback.format_exc())
# HTML padronizado (traceback escapado em <pre>), assunto "ERRO RPA <robot_id>: <title>", vai para @dev

mail.send_or_raise(...)          # levanta EmailSendError(result) se não enviar
mail.check()                     # (True, {robot_id, groups, ...}) ou (False, {error_code, ...})
mail.get_status(email_id)        # resultado de um envio anterior deste robô
```

### Integração com a lib de logs (opcional)

```python
mail = EmailClient(config, logger=rpa_logger)   # RPALoggerClient da trajetoria_logger
```

Com o logger, a lib registra `info("e-mail enviado", email_id=…)` / `error("falha no envio de
e-mail", error_code=…)` e usa `logger.execution_id` como `execution_id` padrão. Não há import
obrigatório de `trajetoria_logger`.

## `SendResult`

| Campo | |
|---|---|
| `ok` | `True` se `status == "sent"` |
| `status` | `sent` \| `failed` (a API respondeu, o envio falhou) \| `error` (validação local ou API sem resposta) |
| `email_id` | id na auditoria do servidor |
| `error_code`, `error_detail` | ver tabela abaixo |
| `recipients` | destinatários **efetivos** (após grupos e modo) |
| `attachments` | `[{filename, size, content_type}]` efetivamente anexados |
| `message_id`, `warnings`, `attempts`, `http_status`, `idempotent_replay` | |

### `error_code`

| Código | Origem | Significado | O que fazer |
|---|---|---|---|
| `CLIENT_VALIDATION` | lib | sem destinatário, sem corpo, assunto inválido, `mode` inválido | corrigir a chamada |
| `ATTACHMENT_NOT_FOUND` | lib | anexo inexistente com `strict_attachments=True` | |
| `ATTACHMENTS_TOO_LARGE` / `TOO_MANY_ATTACHMENTS` | lib/API | > 18 MB / > 20 arquivos | `compress="auto"` ou dividir |
| `ATTACHMENT_BLOCKED` / `ATTACHMENT_EMPTY` | lib/API | executável / arquivo de 0 bytes | |
| `API_UNREACHABLE` | lib | API fora, DNS, timeout, 502–504 (após as retentativas) | o robô segue; ver contingência |
| `RATE_LIMITED_API` | lib | 429 da API após as retentativas | |
| `KEY_INVALID` / `KEY_INACTIVE` | API | chave desconhecida / desativada (kill switch) | falar com a Trajetória |
| `GROUP_NOT_FOUND`, `RECIPIENT_NOT_ALLOWED`, `PROFILE_NOT_ALLOWED`, `INVALID_ADDRESS`, `IDEMPOTENCY_CONFLICT`… | API | erro de requisição (código do servidor) | corrigir chamada/cadastro |
| `REQUEST_INVALID` | API | 4xx sem código específico (ex.: 422) | |
| `SMTP_AUTH_FAILED` | servidor | senha do perfil inválida/revogada | admin: `profiles/{nome}/test` |
| `SMTP_RECIPIENT_REFUSED` / `SMTP_SENDER_REFUSED` / `SMTP_MESSAGE_REJECTED` | servidor | recusado pelo provedor | |
| `SMTP_RATE_LIMITED` | servidor | limite diário do provedor (Gmail) | não adianta retentar agora |
| `SMTP_CONNECT_FAILED` / `SMTP_TEMPORARY` | servidor | o servidor já retentou | |
| `PROFILE_DISABLED` / `PROFILE_MISCONFIGURED` | servidor | perfil desligado / sem senha no servidor | admin |
| `INTERNAL_ERROR` / `API_ERROR` / `CLIENT_ERROR` | — | bug | reportar |

Recusa **parcial** de destinatários volta `ok=True` com o recusado em `warnings`.

## Migração: de `smtplib` para a lib

**Antes** (cada projeto):

```python
# .env: EMAIL_REMETENTE, EMAIL_REMETENTE_SENHA, EMAIL_REMETENTE_SMTP_HOST/PORTA, EMAIL_DESTINATARIOS_*
def send_report(subject, html_body, to, cc=None, attachments=None) -> bool:
    msg = EmailMessage()
    msg["Subject"], msg["From"], msg["To"] = subject, settings.EMAIL_REMETENTE, ", ".join(to)
    if cc:
        msg["Cc"] = ", ".join(cc)
    msg.set_content("Veja a versão HTML.")
    msg.add_alternative(html_body, subtype="html")
    for path in attachments or []:
        if not os.path.exists(path):
            log.warning("anexo não encontrado: %s", path)
            continue
        with open(path, "rb") as f:
            msg.add_attachment(f.read(), maintype="application",
                               subtype="vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                               filename=os.path.basename(path))
    try:
        with smtplib.SMTP_SSL(settings.SMTP_HOST, settings.SMTP_PORT, timeout=30) as s:
            s.login(settings.EMAIL_REMETENTE, settings.EMAIL_REMETENTE_SENHA)
            s.send_message(msg)
        return True
    except Exception:
        log.exception("falha no envio")
        return False
```

**Depois**:

```python
# .env: EMAIL_API_KEY, EMAIL_API_URL
_mail = EmailClient(EmailConfig(
    api_key=settings.EMAIL_API_KEY, api_base_url=settings.EMAIL_API_URL,
    mode="prod" if settings.AMBIENTE == "PROD" else "hml",
    on_warning=lambda m: log.warning("[email] %s", m),
))

def send_report(subject, html_body, to, cc=None, attachments=None) -> bool:
    r = _mail.send(subject=subject, to=to, cc=cc, html=html_body, attachments=attachments)
    if not r.ok:
        log.error("falha no envio: %s %s", r.error_code, r.error_detail)
    return r.ok
```

Passos:

1. Pedir à Trajetória a chave do robô e o cadastro dos grupos (`cliente`, `trajetoria`, `dev`)
   com os endereços que hoje estão no `.env`.
2. `requirements.txt` += `trajetoria-email @ git+https://…@v0.1.0`.
3. `.env`: `EMAIL_API_KEY`, `EMAIL_API_URL`. Remover `EMAIL_REMETENTE*`/senhas e, após a
   homologação, `EMAIL_DESTINATARIOS_*`.
4. Trocar listas do `.env` por `["@cliente"]`, `["@trajetoria"]`, `["@dev"]`.
5. Trocar `dev_only`/`MODE`/`report_send_dev` por `mode="dev"`/`"hml"`.
6. Homologar com `mode="hml"` (tudo vai para `@dev`), depois `mode="prod"`.

## Desenvolvimento

```bash
pip install -e ".[dev]"
pytest
python -m build            # gera o wheel em dist/ (README vai dentro do pacote)
```
