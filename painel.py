"""Painel (dashboard) da tela inicial: filtro de período, cartões e gráficos."""

from datetime import timedelta
from decimal import Decimal

import altair as alt
import pandas as pd
import streamlit as st

from db import transacao
from formatacao import brl, fmt_data, hoje_brasil

ROSA = "#D6577A"
VINHO = "#7A2E48"
PALETA = ["#D6577A", "#7A2E48", "#F4A6B8", "#B08BBB", "#F2B880", "#8DB3A6", "#9A8C98", "#E58AA3"]

SERIE_DESPESAS = "Despesas a pagar"
SERIE_FORNECEDORAS = "Fornecedoras a pagar"

TOP_TIPOS = 7
TOP_TAMANHOS = 12


# ----------------------------------------------------------------
# Leitura (uma conexão só para o painel inteiro)
# ----------------------------------------------------------------
def _carregar(inicio, fim):
    with transacao() as q:
        estoque = q(
            "SELECT COUNT(*) AS pecas, COALESCE(SUM(preco_custo), 0) AS capital "
            "FROM produto WHERE status = 'em_estoque'",
            fetch=True,
        )[0]

        vendas = q(
            "SELECT COUNT(*) AS qtd, COALESCE(SUM(valor_total), 0) AS receita "
            "FROM venda WHERE data_venda::date BETWEEN %s AND %s",
            (inicio, fim),
            fetch=True,
        )[0]

        itens = q(
            """
            SELECT COUNT(*) AS pecas, COALESCE(SUM(p.preco_custo), 0) AS custo
            FROM item_venda iv
            JOIN venda v ON v.id = iv.venda_id
            JOIN produto p ON p.id = iv.produto_id
            WHERE v.data_venda::date BETWEEN %s AND %s
            """,
            (inicio, fim),
            fetch=True,
        )[0]

        # devoluções entram na data da devolução: tiram receita e devolvem o custo da peça
        devolucoes = q(
            """
            SELECT (SELECT COUNT(*) FROM devolucao WHERE data BETWEEN %s AND %s) AS qtd,
                   (SELECT COALESCE(SUM(valor_devolvido), 0) FROM devolucao WHERE data BETWEEN %s AND %s) AS valor,
                   COUNT(di.id) AS pecas,
                   COALESCE(SUM(p.preco_custo), 0) AS custo
            FROM devolucao d
            JOIN devolucao_item di ON di.devolucao_id = d.id
            JOIN produto p ON p.id = di.produto_id
            WHERE d.data BETWEEN %s AND %s
            """,
            (inicio, fim, inicio, fim, inicio, fim),
            fetch=True,
        )[0]

        despesas = q(
            """
            SELECT COALESCE(SUM(valor), 0) AS total,
                   COALESCE(SUM(valor) FILTER (WHERE status_pagamento = 'pendente'), 0) AS a_pagar,
                   COUNT(*) FILTER (WHERE status_pagamento = 'pendente') AS a_pagar_qtd,
                   COALESCE(SUM(valor) FILTER (WHERE status_pagamento = 'pago'), 0) AS pagas,
                   COUNT(*) FILTER (WHERE status_pagamento = 'pago') AS pagas_qtd
            FROM despesa
            WHERE data BETWEEN %s AND %s
            """,
            (inicio, fim),
            fetch=True,
        )[0]

        # compras de peças (fornecedoras, bazar...): por parcela
        fornecedoras_a_pagar = q(
            """
            SELECT COALESCE(SUM(valor), 0) AS total, COUNT(*) AS qtd
            FROM compra_parcela
            WHERE status = 'pendente' AND data_vencimento BETWEEN %s AND %s
            """,
            (inicio, fim),
            fetch=True,
        )[0]

        # pagas: já com o desconto do pagamento rateado entre as compras
        fornecedoras_pagas = q(
            """
            SELECT COALESCE(SUM(
                       CASE WHEN p.baixa_id IS NULL THEN p.valor
                            WHEN b.valor_bruto = 0 THEN 0
                            ELSE p.valor * b.valor_pago / b.valor_bruto END
                   ), 0) AS total,
                   COUNT(*) AS qtd
            FROM compra_parcela p
            LEFT JOIN baixa_compra b ON b.id = p.baixa_id
            WHERE p.status = 'pago'
              AND p.data_pagamento BETWEEN %s AND %s
            """,
            (inicio, fim),
            fetch=True,
        )[0]

        serie_despesas = q(
            "SELECT data AS dia, SUM(valor) AS total FROM despesa "
            "WHERE status_pagamento = 'pendente' AND data BETWEEN %s AND %s GROUP BY data",
            (inicio, fim),
            fetch=True,
        )
        serie_fornecedoras = q(
            "SELECT data_vencimento AS dia, SUM(valor) AS total FROM compra_parcela "
            "WHERE status = 'pendente' "
            "AND data_vencimento BETWEEN %s AND %s GROUP BY data_vencimento",
            (inicio, fim),
            fetch=True,
        )
        serie_vendas = q(
            """
            SELECT dia, SUM(total) AS total FROM (
                SELECT v.data_venda::date AS dia, v.valor_total AS total FROM venda v
                WHERE v.data_venda::date BETWEEN %s AND %s
                UNION ALL
                SELECT d.data, -d.valor_devolvido FROM devolucao d
                WHERE d.data BETWEEN %s AND %s
            ) x GROUP BY dia
            """,
            (inicio, fim, inicio, fim),
            fetch=True,
        )

        tipos = q(
            """
            SELECT COALESCE(tp.nome, 'Sem tipo') AS nome, COUNT(*) AS qtd
            FROM item_venda iv
            JOIN venda v ON v.id = iv.venda_id
            JOIN produto p ON p.id = iv.produto_id
            LEFT JOIN tipo_peca tp ON tp.id = p.tipo_peca_id
            WHERE v.data_venda::date BETWEEN %s AND %s
              AND NOT EXISTS (SELECT 1 FROM devolucao_item di WHERE di.item_venda_id = iv.id)
            GROUP BY 1 ORDER BY qtd DESC, nome
            """,
            (inicio, fim),
            fetch=True,
        )
        tamanhos = q(
            """
            SELECT COALESCE(NULLIF(btrim(p.tamanho), ''), 'Sem tamanho') AS nome, COUNT(*) AS qtd
            FROM item_venda iv
            JOIN venda v ON v.id = iv.venda_id
            JOIN produto p ON p.id = iv.produto_id
            WHERE v.data_venda::date BETWEEN %s AND %s
              AND NOT EXISTS (SELECT 1 FROM devolucao_item di WHERE di.item_venda_id = iv.id)
            GROUP BY 1 ORDER BY qtd DESC, nome
            """,
            (inicio, fim),
            fetch=True,
        )

    return {
        "estoque": estoque,
        "vendas": vendas,
        "itens": itens,
        "devolucoes": devolucoes,
        "despesas": despesas,
        "fornecedoras_a_pagar": fornecedoras_a_pagar,
        "fornecedoras_pagas": fornecedoras_pagas,
        "serie_despesas": serie_despesas,
        "serie_fornecedoras": serie_fornecedoras,
        "serie_vendas": serie_vendas,
        "tipos": tipos,
        "tamanhos": tamanhos,
    }


# ----------------------------------------------------------------
# Gráficos
# ----------------------------------------------------------------
def _dias(inicio, fim):
    return pd.date_range(inicio, fim, freq="D")


def _grafico_a_pagar_por_dia(dados, inicio, fim):
    por_dia = {
        SERIE_DESPESAS: {l["dia"]: float(l["total"]) for l in dados["serie_despesas"]},
        SERIE_FORNECEDORAS: {l["dia"]: float(l["total"]) for l in dados["serie_fornecedoras"]},
    }
    linhas = []
    for dia in _dias(inicio, fim):
        for serie, valores in por_dia.items():
            valor = valores.get(dia.date(), 0.0)
            linhas.append(
                {"dia": dia, "dia_txt": fmt_data(dia), "serie": serie, "valor": valor, "valor_txt": brl(valor)}
            )
    df = pd.DataFrame(linhas)
    if df["valor"].sum() == 0:
        st.info("Sem despesas nem fornecedoras a pagar nesse período.")
        return

    st.altair_chart(
        alt.Chart(df)
        .mark_bar()
        .encode(
            # eixo por dia (categórico): cada barra fica centralizada no próprio dia
            x=alt.X(
                "yearmonthdate(dia):O",
                title="Dia",
                axis=alt.Axis(format="%d/%m", labelAngle=-90, labelOverlap=True, grid=False),
            ),
            y=alt.Y("valor:Q", title="R$ a pagar", stack="zero", axis=alt.Axis(format="~s")),
            color=alt.Color(
                "serie:N",
                title=None,
                scale=alt.Scale(domain=[SERIE_DESPESAS, SERIE_FORNECEDORAS], range=[ROSA, VINHO]),
                legend=alt.Legend(orient="top"),
            ),
            tooltip=[
                alt.Tooltip("dia_txt:N", title="Dia"),
                alt.Tooltip("serie:N", title="Tipo"),
                alt.Tooltip("valor_txt:N", title="Valor"),
            ],
        )
        .properties(height=320),
        use_container_width=True,
    )


def _grafico_vendas_por_dia(dados, inicio, fim):
    por_dia = {l["dia"]: float(l["total"]) for l in dados["serie_vendas"]}
    df = pd.DataFrame(
        [
            {"dia": dia, "dia_txt": fmt_data(dia), "valor": por_dia.get(dia.date(), 0.0),
             "valor_txt": brl(por_dia.get(dia.date(), 0.0))}
            for dia in _dias(inicio, fim)
        ]
    )
    if (df["valor"] == 0).all():
        st.info("Sem vendas nesse período.")
        return

    st.altair_chart(
        alt.Chart(df)
        .mark_line(color=ROSA, point=alt.OverlayMarkDef(filled=True, size=45, color=ROSA))
        .encode(
            x=alt.X("yearmonthdate(dia):T", title="Dia", axis=alt.Axis(format="%d/%m", labelAngle=-45, grid=False)),
            y=alt.Y("valor:Q", title="Vendas (R$)", axis=alt.Axis(format="~s")),
            tooltip=[alt.Tooltip("dia_txt:N", title="Dia"), alt.Tooltip("valor_txt:N", title="Vendas")],
        )
        .properties(height=300),
        use_container_width=True,
    )


def _agrupar_top(linhas, limite, rotulo_demais):
    """Mantém os 'limite' maiores e soma o resto em uma fatia 'demais'."""
    principais = [(l["nome"], l["qtd"]) for l in linhas[:limite]]
    resto = sum(l["qtd"] for l in linhas[limite:])
    if resto:
        principais.append((rotulo_demais, resto))
    return principais


def _grafico_pizza_tipos(dados):
    itens = _agrupar_top(dados["tipos"], TOP_TIPOS, "Demais tipos")
    if not itens:
        st.info("Sem vendas nesse período.")
        return
    total = sum(qtd for _, qtd in itens)
    df = pd.DataFrame(
        [
            {"tipo": nome, "qtd": qtd, "rotulo": f"{nome} — {qtd} ({qtd / total * 100:.0f}%)"}
            for nome, qtd in itens
        ]
    )
    st.altair_chart(
        alt.Chart(df)
        .mark_arc(outerRadius=115, stroke="white", strokeWidth=1)
        .encode(
            theta=alt.Theta("qtd:Q", stack=True),
            color=alt.Color(
                "rotulo:N",
                title="Tipo de peça",
                sort=list(df["rotulo"]),
                scale=alt.Scale(domain=list(df["rotulo"]), range=PALETA),
                legend=alt.Legend(orient="right", labelLimit=220),
            ),
            order=alt.Order("qtd:Q", sort="descending"),
            tooltip=[alt.Tooltip("tipo:N", title="Tipo"), alt.Tooltip("qtd:Q", title="Peças vendidas")],
        )
        .properties(height=300),
        use_container_width=True,
    )


def _grafico_barras_tamanhos(dados):
    linhas = dados["tamanhos"][:TOP_TAMANHOS]
    if not linhas:
        st.info("Sem vendas nesse período.")
        return
    df = pd.DataFrame([{"tamanho": l["nome"], "qtd": l["qtd"]} for l in linhas])
    barras = (
        alt.Chart(df)
        .mark_bar(color=ROSA, cornerRadiusTopLeft=3, cornerRadiusTopRight=3)
        .encode(
            x=alt.X("tamanho:N", sort=list(df["tamanho"]), title="Tamanho", axis=alt.Axis(labelAngle=0)),
            y=alt.Y("qtd:Q", title="Peças vendidas", axis=alt.Axis(tickMinStep=1, format="d")),
            tooltip=[alt.Tooltip("tamanho:N", title="Tamanho"), alt.Tooltip("qtd:Q", title="Peças vendidas")],
        )
    )
    rotulos = barras.mark_text(dy=-8, color="#2B2320").encode(text="qtd:Q")
    st.altair_chart((barras + rotulos).properties(height=300), use_container_width=True)


# ----------------------------------------------------------------
# Tela
# ----------------------------------------------------------------
def _cartao(coluna, titulo, valor, legenda, ajuda=None):
    with coluna.container(border=True):
        st.metric(titulo, valor, help=ajuda)
        st.caption(legenda)


def renderizar_painel():
    hoje = hoje_brasil()
    primeiro_dia = hoje.replace(day=1)
    ultimo_dia = (primeiro_dia + timedelta(days=32)).replace(day=1) - timedelta(days=1)

    periodo = st.date_input(
        "Período", value=(primeiro_dia, ultimo_dia), format="DD/MM/YYYY", key="painel_periodo",
        help="Vale para vendas, margem, resultado, despesas e fornecedoras. "
             "Peças e capital em estoque mostram a posição de hoje.",
    )
    if not isinstance(periodo, (tuple, list)) or len(periodo) != 2:
        st.info("Selecione a data inicial e a final do período.")
        return
    inicio, fim = periodo
    if (fim - inicio).days > 366:
        st.warning("Período muito longo para os gráficos diários — escolha até 1 ano.")
        return

    dados = _carregar(inicio, fim)

    estoque, vendas, itens, despesas = dados["estoque"], dados["vendas"], dados["itens"], dados["despesas"]
    devolucoes = dados["devolucoes"]
    receita = Decimal(vendas["receita"]) - Decimal(devolucoes["valor"])
    custo = Decimal(itens["custo"]) - Decimal(devolucoes["custo"])
    lucro_bruto = receita - custo
    margem_pct = (lucro_bruto / receita * 100) if receita else None
    resultado = lucro_bruto - Decimal(despesas["total"])

    linha1 = st.columns(5)
    _cartao(linha1[0], "Peças em estoque", estoque["pecas"], "posição de hoje")
    _cartao(linha1[1], "Capital em estoque", brl(estoque["capital"]), "custo das peças em estoque",
            "Soma do custo pago nas peças que ainda estão em estoque (posição de hoje).")
    legenda_vendas = f"{itens['pecas']} peça(s) vendida(s)"
    if devolucoes["pecas"]:
        legenda_vendas += f" · {devolucoes['pecas']} devolvida(s)"
    _cartao(linha1[2], "Vendas no período", vendas["qtd"], legenda_vendas,
            "Quantidade de vendas registradas no período. Peças devolvidas contam na data da devolução.")
    _cartao(linha1[3], "Receita (valor das vendas)", brl(receita), "líquida, após descontos e devoluções",
            "Valor total das vendas do período, já com os descontos, menos o valor devolvido "
            "às clientes em devoluções feitas no período.")
    _cartao(
        linha1[4], "Margem bruta por peça",
        f"{margem_pct:.1f}%".replace(".", ",") if margem_pct is not None else "—",
        f"lucro bruto {brl(lucro_bruto)}",
        "(Receita − custo das peças vendidas) ÷ Receita. Peças devolvidas saem da receita e do custo.",
    )

    linha2 = st.columns(5)
    _cartao(linha2[0], "Resultado líquido operacional", brl(resultado), "receita − custo − despesas",
            "Receita − custo das peças vendidas − despesas do período (pagas e a pagar). "
            "Compras de peças entram como custo só quando a peça é vendida.")
    _cartao(linha2[1], "Despesas a pagar", brl(despesas["a_pagar"]), f"{despesas['a_pagar_qtd']} lançamento(s)",
            "Despesas pendentes com data dentro do período.")
    _cartao(linha2[2], "Fornecedoras a pagar", brl(dados["fornecedoras_a_pagar"]["total"]),
            f"{dados['fornecedoras_a_pagar']['qtd']} parcela(s)",
            "Parcelas de compras de peças ainda não pagas com vencimento no período (inclui as "
            "já vencidas). Compra à vista conta como 1 parcela; inclui bazar parcelado.")
    _cartao(linha2[3], "Despesas pagas", brl(despesas["pagas"]), f"{despesas['pagas_qtd']} lançamento(s)",
            "Despesas pagas com data dentro do período.")
    _cartao(linha2[4], "Fornecedoras pagas", brl(dados["fornecedoras_pagas"]["total"]),
            f"{dados['fornecedoras_pagas']['qtd']} parcela(s)",
            "Parcelas de compras de peças pagas no período (inclui compras à vista, como bazar), "
            "já com o desconto do pagamento.")

    st.divider()
    st.subheader("Despesas e fornecedoras a pagar por dia")
    _grafico_a_pagar_por_dia(dados, inicio, fim)

    st.subheader("Vendas por dia (líquidas de devoluções)")
    _grafico_vendas_por_dia(dados, inicio, fim)

    col_pizza, col_barras = st.columns(2)
    with col_pizza:
        st.subheader("Tipos de peça mais vendidos")
        _grafico_pizza_tipos(dados)
    with col_barras:
        st.subheader("Tamanhos mais vendidos")
        _grafico_barras_tamanhos(dados)
