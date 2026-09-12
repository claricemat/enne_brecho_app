import streamlit as st
from db import run_query
from branding import aplicar_logo
from auth import exigir_login, botao_logout

st.set_page_config(page_title="ENNE Brechó", page_icon="assets/icone_coracao.png", layout="wide")
aplicar_logo()
exigir_login()
botao_logout()

st.title("ENNE Brechó — Painel")
st.caption("Sistema de controle de estoque, compras, vendas e despesas")

col1, col2, col3, col4, col5 = st.columns(5)

estoque = run_query(
    """
    SELECT COUNT(*) AS total, COALESCE(SUM(preco_custo), 0) AS capital
    FROM produto WHERE status = 'em_estoque'
    """
)[0]

vendas_mes = run_query(
    """
    SELECT COUNT(*) AS total, COALESCE(SUM(preco_vendido), 0) AS receita
    FROM item_venda iv
    JOIN venda v ON v.id = iv.venda_id
    WHERE date_trunc('month', v.data_venda) = date_trunc('month', CURRENT_DATE)
    """
)[0]

despesas_mes = run_query(
    """
    SELECT COALESCE(SUM(valor), 0) AS total
    FROM despesa
    WHERE date_trunc('month', data) = date_trunc('month', CURRENT_DATE)
    """
)[0]["total"]

col1.metric("Peças em estoque", estoque["total"])
col2.metric("Capital em estoque", f"R$ {estoque['capital']:.2f}")
col3.metric("Vendas este mês", vendas_mes["total"])
col4.metric("Receita este mês", f"R$ {vendas_mes['receita']:.2f}")
col5.metric("Despesas este mês", f"R$ {despesas_mes:.2f}")

st.divider()
st.markdown(
    "Use o menu à esquerda para cadastrar fornecedoras, registrar compras, "
    "vendas e despesas, consultar o estoque e gerenciar os cadastros (tipos "
    "de peça, tipos de compra e plano de contas)."
)
