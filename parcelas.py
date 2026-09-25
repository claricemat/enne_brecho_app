"""Parcelamento (compras de peças e despesas).

- gerar_parcelas(): divide um total em N parcelas com vencimentos espaçados;
- editor_parcelas(): bloco de tela para escolher nº de parcelas, 1º vencimento e
  intervalo, com uma tabela onde dá para ajustar o valor e a data de cada parcela.
"""

import calendar
from datetime import date, timedelta
from decimal import ROUND_DOWN, Decimal

import pandas as pd
import streamlit as st

from formatacao import brl

MAX_PARCELAS = 24

INTERVALOS = {
    "Mensal (mesmo dia do mês)": ("meses", 1),
    "A cada 30 dias": ("dias", 30),
    "A cada 15 dias": ("dias", 15),
    "A cada 7 dias": ("dias", 7),
}
INTERVALO_PADRAO = "Mensal (mesmo dia do mês)"

CENTAVO = Decimal("0.01")


def dec(valor):
    """float/str/Decimal -> Decimal com 2 casas."""
    return Decimal(str(valor if valor is not None else 0)).quantize(CENTAVO)


def somar_meses(data, meses):
    """31/01 + 1 mês = 28/02 (ou 29/02): usa o último dia quando o mês é mais curto."""
    mes_total = data.month - 1 + meses
    ano = data.year + mes_total // 12
    mes = mes_total % 12 + 1
    dia = min(data.day, calendar.monthrange(ano, mes)[1])
    return date(ano, mes, dia)


def gerar_parcelas(total, quantidade, primeiro_vencimento, intervalo=INTERVALO_PADRAO):
    """Divide o total em parcelas iguais; os centavos que sobram vão na 1ª parcela.
    Ex.: R$ 100,00 em 3x -> 33,34 + 33,33 + 33,33.
    Retorna lista de dicts: numero, data_vencimento, valor (Decimal)."""
    total = dec(total)
    quantidade = max(int(quantidade), 1)
    base = (total / quantidade).quantize(CENTAVO, rounding=ROUND_DOWN)
    sobra = total - base * quantidade
    unidade, passo = INTERVALOS.get(intervalo, INTERVALOS[INTERVALO_PADRAO])

    parcelas = []
    for i in range(quantidade):
        if unidade == "meses":
            vencimento = somar_meses(primeiro_vencimento, passo * i)
        else:
            vencimento = primeiro_vencimento + timedelta(days=passo * i)
        parcelas.append(
            {"numero": i + 1, "data_vencimento": vencimento, "valor": base + (sobra if i == 0 else 0)}
        )
    return parcelas


def validar_parcelas(parcelas, total, data_minima):
    """Confere a lista de parcelas. Retorna a mensagem de erro, ou None se estiver tudo certo."""
    total = dec(total)
    if not parcelas:
        return "Informe ao menos uma parcela."
    for p in parcelas:
        if p["data_vencimento"] is None:
            return f"Parcela {p['numero']}: informe o vencimento."
        if p["valor"] is None:
            return f"Parcela {p['numero']}: informe o valor."
        if p["valor"] < 0 or (p["valor"] == 0 and total > 0):
            return f"Parcela {p['numero']}: o valor precisa ser maior que zero."
        if data_minima and p["data_vencimento"] < data_minima:
            return (
                f"Parcela {p['numero']}: o vencimento não pode ser antes de "
                f"{data_minima.strftime('%d/%m/%Y')}."
            )
    soma = sum((p["valor"] for p in parcelas), Decimal("0"))
    if soma != total:
        diferenca = total - soma
        return (
            f"A soma das parcelas ({brl(soma)}) precisa ser igual ao total ({brl(total)}). "
            + (f"Faltam {brl(diferenca)}." if diferenca > 0 else f"Sobram {brl(-diferenca)}.")
        )
    return None


def _para_data(valor):
    if valor is None or (isinstance(valor, float) and pd.isna(valor)) or valor is pd.NaT:
        return None
    if isinstance(valor, pd.Timestamp):
        return valor.date()
    return valor


def editor_parcelas(total, data_minima, chave, quantidade_padrao=1, vencimento_padrao=None,
                    iniciais=None, rotulo_quantidade="Número de parcelas"):
    """Bloco de tela do parcelamento.

    total: valor a parcelar; data_minima: nenhum vencimento pode ser antes dela;
    chave: prefixo único das chaves dos campos; iniciais: parcelas já existentes
    (lista de dicts numero/data_vencimento/valor), mostradas enquanto a pessoa não
    mudar o número de parcelas, o 1º vencimento ou o intervalo.

    Retorna (parcelas, erro): parcelas ordenadas por vencimento e numeradas a
    partir de 1; erro é None quando dá para gravar.
    """
    total = dec(total)
    if vencimento_padrao is None:
        vencimento_padrao = max(data_minima, date.today()) + timedelta(days=30)

    col1, col2, col3 = st.columns(3)
    quantidade = int(
        col1.number_input(
            rotulo_quantidade, min_value=1, max_value=MAX_PARCELAS, value=int(quantidade_padrao),
            step=1, key=f"{chave}_qtd",
        )
    )
    primeiro = col2.date_input(
        "1º vencimento", value=vencimento_padrao, min_value=data_minima,
        format="DD/MM/YYYY", key=f"{chave}_venc",
    )
    intervalo = col3.selectbox(
        "Intervalo entre parcelas", list(INTERVALOS), key=f"{chave}_intervalo",
        disabled=quantidade == 1,
    )

    usar_iniciais = (
        iniciais
        and quantidade == int(quantidade_padrao)
        and primeiro == vencimento_padrao
        and intervalo == INTERVALO_PADRAO
    )
    if usar_iniciais:
        sugeridas = [
            {"numero": i + 1, "data_vencimento": p["data_vencimento"], "valor": dec(p["valor"])}
            for i, p in enumerate(iniciais)
        ]
    else:
        sugeridas = gerar_parcelas(total, quantidade, primeiro, intervalo)

    if quantidade == 1 and not usar_iniciais:
        parcelas = sugeridas
    else:
        st.caption("Dá para ajustar o valor e a data de cada parcela direto na tabela.")
        tabela = st.data_editor(
            pd.DataFrame(
                [
                    {"numero": p["numero"], "data_vencimento": p["data_vencimento"], "valor": float(p["valor"])}
                    for p in sugeridas
                ]
            ),
            hide_index=True,
            use_container_width=True,
            num_rows="fixed",
            disabled=["numero"],
            key=f"{chave}_tabela_{quantidade}_{primeiro}_{intervalo}_{total}_{bool(usar_iniciais)}",
            column_config={
                "numero": st.column_config.NumberColumn("Parcela", format="%d"),
                "data_vencimento": st.column_config.DateColumn(
                    "Vencimento", format="DD/MM/YYYY", min_value=data_minima, required=True
                ),
                "valor": st.column_config.NumberColumn(
                    "Valor (R$)", min_value=0.0, step=1.0, format="%.2f", required=True
                ),
            },
        )
        parcelas = [
            {
                "numero": int(linha["numero"]),
                "data_vencimento": _para_data(linha["data_vencimento"]),
                "valor": None if pd.isna(linha["valor"]) else dec(linha["valor"]),
            }
            for _, linha in tabela.iterrows()
        ]

    erro = validar_parcelas(parcelas, total, data_minima)
    if not erro:
        # grava sempre em ordem de vencimento, numeradas 1, 2, 3...
        parcelas = sorted(parcelas, key=lambda p: (p["data_vencimento"], p["numero"]))
        parcelas = [dict(p, numero=i + 1) for i, p in enumerate(parcelas)]
        if len(parcelas) > 1:
            st.caption(
                f"{len(parcelas)} parcelas: "
                + " · ".join(f"{p['data_vencimento'].strftime('%d/%m/%Y')} {brl(p['valor'])}" for p in parcelas[:6])
                + (" · …" if len(parcelas) > 6 else "")
            )
    return parcelas, erro
