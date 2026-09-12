import pandas as pd
import streamlit as st

from db import run_query
from branding import aplicar_logo

st.set_page_config(page_title="Estoque", page_icon="assets/icone_coracao.png", layout="wide")
aplicar_logo()
st.title("Estoque")

produtos = run_query(
    """
    SELECT
        p.id,
        p.descricao,
        tp.nome AS tipo_peca,
        p.tamanho,
        p.preco_custo,
        p.preco_venda,
        p.status,
        p.compra_id AS lote_id,
        tc.nome AS tipo_compra,
        COALESCE(f.nome, '—') AS fornecedora,
        c.data_aceite AS data_compra,
        p.criado_em
    FROM produto p
    LEFT JOIN tipo_peca tp ON tp.id = p.tipo_peca_id
    JOIN compra c ON c.id = p.compra_id
    JOIN tipo_compra tc ON tc.id = c.tipo_compra_id
    LEFT JOIN fornecedora f ON f.id = c.fornecedora_id
    ORDER BY p.criado_em DESC
    """
)

if not produtos:
    st.info("Nenhuma peça cadastrada ainda.")
    st.stop()

df = pd.DataFrame(produtos)

col1, col2, col3, col4 = st.columns(4)
em_estoque_df = df[df["status"] == "em_estoque"]
col1.metric("Peças em estoque", len(em_estoque_df))
col2.metric("Peças vendidas", len(df[df["status"] == "vendido"]))
col3.metric("Capital parado (custo)", f"R$ {em_estoque_df['preco_custo'].sum():.2f}")
col4.metric("Valor de venda potencial", f"R$ {em_estoque_df['preco_venda'].sum():.2f}")

st.divider()

busca = st.text_input("Buscar por descrição", placeholder="ex: vestido")

col1, col2, col3 = st.columns(3)
status_filtro = col1.multiselect(
    "Status", options=["em_estoque", "vendido"], default=["em_estoque"]
)
tipos_disponiveis = sorted(df["tipo_peca"].dropna().unique().tolist())
tipo_filtro = col2.multiselect("Tipo de peça", options=tipos_disponiveis)
fornecedoras_disponiveis = sorted(df["fornecedora"].dropna().unique().tolist())
fornecedora_filtro = col3.multiselect("Fornecedora", options=fornecedoras_disponiveis)

resultado = df.copy()
if busca:
    resultado = resultado[resultado["descricao"].str.contains(busca, case=False, na=False)]
if status_filtro:
    resultado = resultado[resultado["status"].isin(status_filtro)]
if tipo_filtro:
    resultado = resultado[resultado["tipo_peca"].isin(tipo_filtro)]
if fornecedora_filtro:
    resultado = resultado[resultado["fornecedora"].isin(fornecedora_filtro)]

st.caption(f"{len(resultado)} peça(s) encontrada(s)")
st.dataframe(
    resultado[
        [
            "id",
            "descricao",
            "tipo_peca",
            "tamanho",
            "preco_custo",
            "preco_venda",
            "status",
            "lote_id",
            "tipo_compra",
            "fornecedora",
            "data_compra",
        ]
    ],
    use_container_width=True,
    hide_index=True,
)
