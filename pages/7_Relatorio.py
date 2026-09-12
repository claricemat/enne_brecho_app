from datetime import date
from io import BytesIO

import pandas as pd
import streamlit as st

from db import run_query
from branding import aplicar_logo
from auth import exigir_login, botao_logout

st.set_page_config(page_title="Relatório financeiro", page_icon="assets/icone_coracao.png", layout="wide")
aplicar_logo()
exigir_login()
botao_logout()
st.title("Relatório financeiro")

hoje = date.today()
col1, col2 = st.columns(2)
data_inicio = col1.date_input("De", value=hoje.replace(day=1))
data_fim = col2.date_input("Até", value=hoje)

if data_inicio > data_fim:
    st.error("A data inicial não pode ser depois da data final.")
    st.stop()

# ------------------------------------------------------------
# Resumo do período
# ------------------------------------------------------------

receita_resumo = run_query(
    """
    SELECT COUNT(*) AS qtd, COALESCE(SUM(valor_total), 0) AS liquida,
           COALESCE(SUM(desconto), 0) AS descontos
    FROM venda
    WHERE data_venda::date BETWEEN %s AND %s
    """,
    (data_inicio, data_fim),
)[0]

cpv_total = run_query(
    """
    SELECT COALESCE(SUM(p.preco_custo), 0) AS total
    FROM item_venda iv
    JOIN venda v ON v.id = iv.venda_id
    JOIN produto p ON p.id = iv.produto_id
    WHERE v.data_venda::date BETWEEN %s AND %s
    """,
    (data_inicio, data_fim),
)[0]["total"]

despesas_resumo = run_query(
    """
    SELECT COUNT(*) AS qtd, COALESCE(SUM(valor), 0) AS total
    FROM despesa
    WHERE data BETWEEN %s AND %s
    """,
    (data_inicio, data_fim),
)[0]

receita_liquida = float(receita_resumo["liquida"])
cpv = float(cpv_total)
despesas_total = float(despesas_resumo["total"])
margem_bruta = receita_liquida - cpv
margem_pct = (margem_bruta / receita_liquida * 100) if receita_liquida else 0.0
resultado_liquido = margem_bruta - despesas_total

st.subheader("Resumo do período")
col1, col2, col3, col4 = st.columns(4)
col1.metric("Receita líquida", f"R$ {receita_liquida:.2f}", f"{receita_resumo['qtd']} venda(s)")
col2.metric("Custo das peças vendidas", f"R$ {cpv:.2f}")
col3.metric("Margem bruta", f"R$ {margem_bruta:.2f}", f"{margem_pct:.1f}%")
col4.metric("Despesas", f"R$ {despesas_total:.2f}", f"{despesas_resumo['qtd']} lançamento(s)")

st.metric("Resultado líquido do período", f"R$ {resultado_liquido:.2f}")
st.caption(f"Descontos concedidos no período: R$ {float(receita_resumo['descontos']):.2f}")

# ------------------------------------------------------------
# Situação de contas em aberto (independe do período escolhido)
# ------------------------------------------------------------

st.divider()
st.subheader("Contas em aberto (hoje)")

compras_pendentes = run_query(
    "SELECT COUNT(*) AS qtd, COALESCE(SUM(valor_total), 0) AS total FROM compra WHERE status = 'pendente'"
)[0]
despesas_pendentes = run_query(
    "SELECT COUNT(*) AS qtd, COALESCE(SUM(valor), 0) AS total FROM despesa WHERE status_pagamento = 'pendente'"
)[0]

col1, col2 = st.columns(2)
col1.metric(
    "A pagar a fornecedoras",
    f"R$ {float(compras_pendentes['total']):.2f}",
    f"{compras_pendentes['qtd']} compra(s)",
)
col2.metric(
    "Despesas pendentes",
    f"R$ {float(despesas_pendentes['total']):.2f}",
    f"{despesas_pendentes['qtd']} lançamento(s)",
)

# ------------------------------------------------------------
# Composição por plano de contas
# ------------------------------------------------------------

st.divider()
col1, col2 = st.columns(2)

with col1:
    st.subheader("Receita por tipo")
    receita_por_tipo = run_query(
        """
        SELECT COALESCE(pc.nome, 'Sem classificação') AS conta, COALESCE(SUM(v.valor_total), 0) AS total
        FROM venda v
        LEFT JOIN plano_contas pc ON pc.id = v.plano_conta_id
        WHERE v.data_venda::date BETWEEN %s AND %s
        GROUP BY conta
        ORDER BY total DESC
        """,
        (data_inicio, data_fim),
    )
    if receita_por_tipo:
        df_receita = pd.DataFrame(receita_por_tipo).set_index("conta")
        df_receita["total"] = df_receita["total"].astype(float)
        st.bar_chart(df_receita["total"])
    else:
        st.info("Sem vendas no período.")

with col2:
    st.subheader("Despesas por categoria")
    despesas_por_conta = run_query(
        """
        SELECT pc.nome AS conta, COALESCE(SUM(d.valor), 0) AS total
        FROM despesa d
        JOIN plano_contas pc ON pc.id = d.plano_conta_id
        WHERE d.data BETWEEN %s AND %s
        GROUP BY conta
        ORDER BY total DESC
        """,
        (data_inicio, data_fim),
    )
    if despesas_por_conta:
        df_despesas = pd.DataFrame(despesas_por_conta).set_index("conta")
        df_despesas["total"] = df_despesas["total"].astype(float)
        st.bar_chart(df_despesas["total"])
    else:
        st.info("Sem despesas no período.")

# ------------------------------------------------------------
# Tendência dos últimos 6 meses (independe do período escolhido)
# ------------------------------------------------------------

st.divider()
st.subheader("Tendência — últimos 6 meses")

receita_mensal = run_query(
    """
    SELECT date_trunc('month', data_venda)::date AS mes, COALESCE(SUM(valor_total), 0) AS receita
    FROM venda
    WHERE data_venda >= date_trunc('month', CURRENT_DATE) - INTERVAL '5 months'
    GROUP BY 1 ORDER BY 1
    """
)
despesas_mensal = run_query(
    """
    SELECT date_trunc('month', data)::date AS mes, COALESCE(SUM(valor), 0) AS despesas
    FROM despesa
    WHERE data >= date_trunc('month', CURRENT_DATE) - INTERVAL '5 months'
    GROUP BY 1 ORDER BY 1
    """
)
cpv_mensal = run_query(
    """
    SELECT date_trunc('month', v.data_venda)::date AS mes, COALESCE(SUM(p.preco_custo), 0) AS cpv
    FROM item_venda iv
    JOIN venda v ON v.id = iv.venda_id
    JOIN produto p ON p.id = iv.produto_id
    WHERE v.data_venda >= date_trunc('month', CURRENT_DATE) - INTERVAL '5 months'
    GROUP BY 1 ORDER BY 1
    """
)

meses_idx = pd.period_range(end=pd.Timestamp(hoje).to_period("M"), periods=6, freq="M")
tendencia = pd.DataFrame(index=meses_idx)
tendencia.index.name = "mes"

for dados, coluna in [(receita_mensal, "receita"), (despesas_mensal, "despesas"), (cpv_mensal, "cpv")]:
    if dados:
        serie = (
            pd.DataFrame(dados)
            .assign(mes=lambda d: pd.to_datetime(d["mes"]).dt.to_period("M"))
            .set_index("mes")[coluna]
            .astype(float)
        )
        tendencia[coluna] = serie
    else:
        tendencia[coluna] = 0.0

tendencia = tendencia.fillna(0.0)
tendencia["resultado"] = tendencia["receita"] - tendencia["cpv"] - tendencia["despesas"]
tendencia.index = tendencia.index.strftime("%b/%Y")

st.bar_chart(tendencia[["receita", "despesas", "resultado"]])

# ------------------------------------------------------------
# Exportar em Excel
# ------------------------------------------------------------

st.divider()
st.subheader("Exportar")

vendas_detalhe = run_query(
    """
    SELECT v.id, v.data_venda, v.cliente, v.forma_pagamento, v.desconto, v.valor_total,
           COALESCE(pc.nome, 'Sem classificação') AS tipo_receita
    FROM venda v
    LEFT JOIN plano_contas pc ON pc.id = v.plano_conta_id
    WHERE v.data_venda::date BETWEEN %s AND %s
    ORDER BY v.data_venda
    """,
    (data_inicio, data_fim),
)
despesas_detalhe = run_query(
    """
    SELECT d.id, d.data, pc.nome AS categoria, d.descricao, d.valor, d.status_pagamento
    FROM despesa d
    JOIN plano_contas pc ON pc.id = d.plano_conta_id
    WHERE d.data BETWEEN %s AND %s
    ORDER BY d.data
    """,
    (data_inicio, data_fim),
)
compras_detalhe = run_query(
    """
    SELECT c.id, tc.nome AS tipo_compra, COALESCE(f.nome, '—') AS fornecedora,
           c.data_aceite, c.valor_total, c.status, c.data_vencimento, c.data_pagamento
    FROM compra c
    JOIN tipo_compra tc ON tc.id = c.tipo_compra_id
    LEFT JOIN fornecedora f ON f.id = c.fornecedora_id
    WHERE c.data_aceite BETWEEN %s AND %s
    ORDER BY c.data_aceite
    """,
    (data_inicio, data_fim),
)

resumo_df = pd.DataFrame(
    [
        {"indicador": "Receita líquida", "valor": receita_liquida},
        {"indicador": "Custo das peças vendidas", "valor": cpv},
        {"indicador": "Margem bruta", "valor": margem_bruta},
        {"indicador": "Margem bruta (%)", "valor": round(margem_pct, 1)},
        {"indicador": "Despesas", "valor": despesas_total},
        {"indicador": "Resultado líquido", "valor": resultado_liquido},
        {"indicador": "Descontos concedidos", "valor": float(receita_resumo["descontos"])},
    ]
)

buffer = BytesIO()
with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
    resumo_df.to_excel(writer, sheet_name="Resumo", index=False)

    vendas_df = pd.DataFrame(vendas_detalhe or [])
    if not vendas_df.empty and "data_venda" in vendas_df.columns:
        vendas_df["data_venda"] = pd.to_datetime(vendas_df["data_venda"]).dt.tz_localize(None)
    vendas_df.to_excel(writer, sheet_name="Vendas", index=False)

    pd.DataFrame(despesas_detalhe or []).to_excel(writer, sheet_name="Despesas", index=False)
    pd.DataFrame(compras_detalhe or []).to_excel(writer, sheet_name="Compras", index=False)
buffer.seek(0)

st.download_button(
    "Baixar relatório (Excel)",
    data=buffer,
    file_name=f"relatorio_enne_brecho_{data_inicio}_a_{data_fim}.xlsx",
    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
)
