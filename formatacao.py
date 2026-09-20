"""Formatação de valores e datas no padrão brasileiro."""

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
