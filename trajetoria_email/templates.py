"""HTML padronizado do `send_error`."""
from __future__ import annotations

import html
from datetime import datetime
from typing import Optional


def error_html(title: str, message: str, robot_id: str, traceback: Optional[str] = None,
               execution_id: Optional[str] = None) -> str:
    when = datetime.now().astimezone().strftime("%d/%m/%Y %H:%M:%S")
    rows = [
        ("Robô", robot_id),
        ("Quando", when),
    ]
    if execution_id:
        rows.append(("Execução", execution_id))
    table = "".join(
        f'<tr><td style="padding:2px 12px 2px 0;color:#555"><b>{html.escape(k)}</b></td>'
        f"<td>{html.escape(str(v))}</td></tr>"
        for k, v in rows
    )
    tb = ""
    if traceback:
        tb = (
            '<p style="margin:16px 0 4px"><b>Detalhes técnicos</b></p>'
            '<pre style="background:#f6f6f6;border:1px solid #ddd;padding:8px;font-size:12px;'
            f'white-space:pre-wrap">{html.escape(traceback)}</pre>'
        )
    msg = html.escape(message).replace("\n", "<br>")
    return (
        '<html><body style="font-family:Arial,Helvetica,sans-serif;font-size:14px;color:#222">'
        f'<h3 style="color:#b00020;margin:0 0 12px">Erro no RPA: {html.escape(title)}</h3>'
        f"<p>{msg}</p>"
        f'<table style="border-collapse:collapse;font-size:13px">{table}</table>'
        f"{tb}"
        '<p style="color:#888;font-size:11px;margin-top:24px">Mensagem automática — trajetoria-email</p>'
        "</body></html>"
    )
