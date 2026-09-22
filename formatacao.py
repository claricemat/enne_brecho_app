"""Formatação de valores e datas no padrão brasileiro."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal


def brl(valor):
    """1234.5 -> 'R$ 1.234,50'."""
    texto = f"{Decimal(str(valor or 0)):,.2f}"  # 1,234.50
    return "R$ " + texto.replace(",", "X").replace(".", ",").replace("X", ".")


def fmt_data(d):
    """date/datetime -> 'dd/mm/aaaa'; vazio -> '—'."""
    if d is None or d == "":
        return "—"
    if hasattr(d, "strftime"):
        return d.strftime("%d/%m/%Y")
    return str(d)


def hoje_brasil():
    """Data de hoje no Brasil (UTC-3). O servidor do Streamlit Cloud roda em UTC:
    à noite, date.today() já seria 'amanhã' e os dias contados ficariam errados."""
    return datetime.now(timezone(timedelta(hours=-3))).date()
