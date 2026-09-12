import pandas as pd
import streamlit as st

from db import run_query
from branding import aplicar_logo

st.set_page_config(page_title="Vendas", page_icon="assets/icone_coracao.png", layout="wide")
aplicar_logo()
st.title("Registrar venda")

if "venda_version" not in st.session_state:
    st.session_state.venda_version = 0

produtos = run_query(
    """
    SELECT p.id, p.descricao, tp.nome AS tipo_peca, p.tamanho, p.preco_venda
    FROM produto p
    LEFT JOIN tipo_peca tp ON tp.id = p.tipo_peca_id
    WHERE p.status = 'em_estoque'
    ORDER BY p.descricao
    """
)

if not produtos:
    st.info("Não há peças em estoque no momento.")
    st.stop()

df_estoque = pd.DataFrame(produtos)
df_estoque.insert(0, "vender", False)
df_estoque["preco_vendido"] = df_estoque["preco_venda"]

busca = st.text_input("Buscar peça (por descrição)", placeholder="ex: camisa azul")
if busca:
    df_estoque = df_estoque[
        df_estoque["descricao"].str.contains(busca, case=False, na=False)
    ].reset_index(drop=True)

st.caption(
    "Marque as peças vendidas nessa transação. O preço vem pré-preenchido com o "
    "preço de venda, mas pode ser ajustado (ex: negociação item a item)."
)

if df_estoque.empty:
    st.warning("Nenhuma peça em estoque bate com essa busca.")
    st.stop()

editado = st.data_editor(
    df_estoque,
    use_container_width=True,
    hide_index=True,
    disabled=["id", "descricao", "tipo_peca", "tamanho", "preco_venda"],
    key=f"editor_venda_{st.session_state.venda_version}_{busca}",
    column_config={
        "vender": st.column_config.CheckboxColumn("Vender?"),
        "preco_vendido": st.column_config.NumberColumn(
            "Preço vendido (R$)", min_value=0.0, step=1.0
        ),
    },
)

selecionados = editado[editado["vender"]]

col1, col2 = st.columns(2)
cliente = col1.text_input("Cliente (opcional)")
forma_pagamento = col2.selectbox(
    "Forma de pagamento",
    ["Dinheiro", "Pix", "Cartão de Crédito", "Cartão de Débito", "Crédito Loja", "Outro"],
)

tipos_receita = run_query("SELECT id, nome FROM plano_contas WHERE tipo = 'receita' ORDER BY nome")
opcoes_receita = {r["nome"]: r["id"] for r in tipos_receita} if tipos_receita else {}
nomes_receita = list(opcoes_receita.keys())
indice_padrao = nomes_receita.index("Venda de peças") if "Venda de peças" in nomes_receita else 0

col3, col4 = st.columns(2)
desconto = col3.number_input("Desconto (R$)", min_value=0.0, step=1.0, value=0.0)
if nomes_receita:
    nome_receita = col4.selectbox("Tipo de receita", options=nomes_receita, index=indice_padrao)
    plano_conta_id = opcoes_receita[nome_receita]
else:
    plano_conta_id = None

if not selecionados.empty:
    subtotal = selecionados["preco_vendido"].sum()
    valor_total_preview = max(subtotal - desconto, 0)
    st.metric("Total da venda", f"R$ {valor_total_preview:.2f}", f"desconto R$ {desconto:.2f}")

if st.button("Registrar venda", type="primary", disabled=selecionados.empty):
    subtotal = round(selecionados["preco_vendido"].sum(), 2)
    valor_total = round(max(subtotal - desconto, 0), 2)

    venda = run_query(
        "INSERT INTO venda (forma_pagamento, valor_total, cliente, desconto, plano_conta_id) "
        "VALUES (%s, %s, %s, %s, %s) RETURNING id",
        (forma_pagamento, valor_total, cliente.strip() or None, desconto, plano_conta_id),
    )
    venda_id = venda[0]["id"]

    for _, item in selecionados.iterrows():
        produto_id = int(item["id"])
        run_query(
            "INSERT INTO item_venda (venda_id, produto_id, preco_vendido) VALUES (%s, %s, %s)",
            (venda_id, produto_id, item["preco_vendido"]),
            fetch=False,
        )
        run_query(
            "UPDATE produto SET status = 'vendido' WHERE id = %s",
            (produto_id,),
            fetch=False,
        )
        run_query(
            "INSERT INTO movimentacao_estoque (produto_id, tipo, observacao) "
            "VALUES (%s, 'saida', %s)",
            (produto_id, f"Saída via venda #{venda_id}"),
            fetch=False,
        )

    st.success(
        f"Venda #{venda_id} registrada — {len(selecionados)} peça(s), "
        f"total R$ {valor_total:.2f} (desconto de R$ {desconto:.2f})."
    )
    st.session_state.venda_version += 1
    st.rerun()

st.divider()
st.subheader("Vendas recentes")

vendas = run_query(
    """
    SELECT v.id, v.data_venda, v.cliente, v.forma_pagamento, v.desconto, v.valor_total,
           pc.nome AS tipo_receita
    FROM venda v
    LEFT JOIN plano_contas pc ON pc.id = v.plano_conta_id
    ORDER BY v.data_venda DESC, v.id DESC LIMIT 30
    """
)
if vendas:
    st.dataframe(vendas, use_container_width=True, hide_index=True)
