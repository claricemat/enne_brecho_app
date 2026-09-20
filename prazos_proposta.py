"""Prazos das duas propostas de compra feitas à fornecedora.

Proposta A (curto prazo): pagamento em até 10 dias.
Proposta B (longo prazo): pagamento em até 30 dias úteis (sem sábados,
domingos e feriados nacionais).

O vencimento calculado aqui é só o ponto de partida: dá pra ajustar a data
na hora de registrar a compra e depois, na página de Compras.
"""

from datetime import timedelta

import holidays

PRAZO_CURTO_DIAS = 10
PRAZO_LONGO_DIAS_UTEIS = 30

ROTULO_CURTO = f"Proposta A — curto prazo (até {PRAZO_CURTO_DIAS} dias)"
ROTULO_LONGO = f"Proposta B — longo prazo (até {PRAZO_LONGO_DIAS_UTEIS} dias úteis)"


def somar_dias_uteis(data_base, dias):
    """Soma `dias` dias úteis a partir do dia seguinte a data_base."""
    feriados = holidays.Brazil(years=range(data_base.year, data_base.year + 3))
    atual = data_base
    restantes = dias
    while restantes > 0:
        atual += timedelta(days=1)
        if atual.weekday() < 5 and atual not in feriados:
            restantes -= 1
    return atual


def vencimento_da_proposta(proposta, data_aceite):
    """proposta: 'curto' ou 'longo'."""
    if proposta == "curto":
        return data_aceite + timedelta(days=PRAZO_CURTO_DIAS)
    if proposta == "longo":
        return somar_dias_uteis(data_aceite, PRAZO_LONGO_DIAS_UTEIS)
    raise ValueError(f"Proposta desconhecida: {proposta!r}")
