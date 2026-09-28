"""Condições das propostas de compra feitas à fornecedora na avaliação.

Proposta à vista: pagamento em até 10 dias.
Proposta parcelada: N parcelas; a 1ª vence X dias úteis após o aceite (sem
sábados, domingos e feriados nacionais) e as demais seguem o intervalo escolhido.

Cada avaliação pode enviar só uma das propostas ou as duas. Os vencimentos
calculados aqui são o ponto de partida: dá pra ajustar tudo na hora de
registrar a compra e depois, na página de Compras.
"""

from datetime import timedelta

import holidays

from formatacao import brl
from parcelas import INTERVALO_POR_CODIGO, dec, gerar_parcelas

PRAZO_A_VISTA_DIAS = 10
PARCELADA_QTD_PADRAO = 3
PARCELADA_PRIMEIRA_DIAS_UTEIS_PADRAO = 30

ROTULO_A_VISTA = f"À vista (pagamento em até {PRAZO_A_VISTA_DIAS} dias)"
ROTULO_PARCELADA = "Parcelada"

# como o intervalo aparece no texto da proposta
INTERVALO_TEXTO = {
    "mensal": "mensais",
    "30d": "a cada 30 dias",
    "15d": "a cada 15 dias",
    "7d": "semanais",
}


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


def vencimento_a_vista(data_aceite):
    return data_aceite + timedelta(days=PRAZO_A_VISTA_DIAS)


def primeiro_vencimento_parcelada(data_aceite, dias_uteis):
    return somar_dias_uteis(data_aceite, int(dias_uteis))


def parcelas_da_proposta(total, config, data_aceite):
    """Parcelas da proposta parcelada para um aceite em data_aceite."""
    return gerar_parcelas(
        total,
        config["parcelada_qtd"],
        primeiro_vencimento_parcelada(data_aceite, config["parcelada_primeira_dias_uteis"]),
        INTERVALO_POR_CODIGO[config["parcelada_intervalo"]],
    )


def descricao_parcelada(total, config):
    """Texto das condições da proposta parcelada, ex.:
    '3 parcelas mensais de R$ 100,00; a 1ª em até 30 dias úteis após o aceite'."""
    qtd = int(config["parcelada_qtd"])
    dias = int(config["parcelada_primeira_dias_uteis"])
    total = dec(total)
    if qtd == 1:
        return f"parcela única de {brl(total)}, em até {dias} dias úteis após o aceite"
    # a mesma divisão usada ao registrar a compra (centavos que sobram vão na 1ª)
    from datetime import date
    valores = [p["valor"] for p in gerar_parcelas(total, qtd, date(2000, 1, 1))]
    if valores[0] == valores[-1]:
        valores_txt = f"{qtd} parcelas {INTERVALO_TEXTO[config['parcelada_intervalo']]} de {brl(valores[-1])}"
    else:
        valores_txt = (
            f"{qtd} parcelas {INTERVALO_TEXTO[config['parcelada_intervalo']]} "
            f"(1ª de {brl(valores[0])} e as demais de {brl(valores[-1])})"
        )
    return f"{valores_txt}; a 1ª em até {dias} dias úteis após o aceite"


def rotulo_parcelada(config):
    qtd = int(config["parcelada_qtd"])
    return f"Parcelada ({qtd}x)" if qtd > 1 else "Parcelada (1x)"
