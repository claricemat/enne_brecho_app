from datetime import date, timedelta

import pandas as pd
import streamlit as st

from db import run_query
from branding import aplicar_logo
from auth import exigir_login, botao_logout

st.set_page_config(page_title="Compras", page_icon="assets/icone_coracao.png", layout="wide")
aplicar_logo()
exigir_login()
botao_logout()
st.title("Registrar compra")

tipos_compra = run_query(
    "SELECT id, nome, prazo_dias, requer_fornecedora FROM tipo_compra ORDER BY id"
)
tipos_peca = run_query("SELECT id, nome FROM tipo_peca ORDER BY nome")

if not tipos_compra:
    st.warning("Nenhum tipo de compra cadastrado. Vá em Cadastros e crie ao menos um.")
    st.stop()
if not tipos_peca:
    st.warning("Nenhum tipo de peça cadastrado. Vá em Cadastros e crie ao menos um.")
    st.stop()

opcoes_tipo_compra = {t["nome"]: t for t in tipos_compra}
nome_tipo_compra = st.selectbox("Tipo de compra", options=list(opcoes_tipo_compra.keys()))
tipo_selecionado = opcoes_tipo_compra[nome_tipo_compra]

fornecedoras = run_query("SELECT id, nome FROM fornecedora ORDER BY nome")
opcoes_fornecedora = {f["nome"]: f["id"] for f in fornecedoras} if fornecedoras else {}

fornecedora_id = None
if tipo_selecionado["requer_fornecedora"]:
    if not opcoes_fornecedora:
        st.warning("Esse tipo de compra exige uma fornecedora. Cadastre uma antes de continuar.")
        st.stop()
    nome_fornecedora = st.selectbox("Fornecedora", options=list(opcoes_fornecedora.keys()))
    fornecedora_id = opcoes_fornecedora[nome_fornecedora]
else:
    opcoes_com_vazio = ["— nenhuma —"] + list(opcoes_fornecedora.keys())
    nome_fornecedora = st.selectbox("Fornecedora (opcional)", options=opcoes_com_vazio)
    if nome_fornecedora != "— nenhuma —":
        fornecedora_id = opcoes_fornecedora[nome_fornecedora]

if tipo_selecionado["prazo_dias"] > 0:
    st.caption(f"Prazo de pagamento: {tipo_selecionado['prazo_dias']} dias após o aceite.")
else:
    st.caption("Compra à vista — já entra como paga na data de hoje.")

data_aceite = st.date_input("Data da compra", value=date.today())

st.subheader("Peças do lote")
st.caption(
    "Preencha uma linha por peça, com o custo real pago (não precisa ser 40% — "
    "varia por fornecedora, bazar etc.). Use o + no fim da tabela pra adicionar linhas."
)

if "compra_version" not in st.session_state:
    st.session_state.compra_version = 0

nomes_tipo_peca = [t["nome"] for t in tipos_peca]

itens = st.data_editor(
    pd.DataFrame(
        [{"descricao": "", "tipo_peca": nomes_tipo_peca[0], "tamanho": "", "preco_venda": 0.0, "preco_custo": 0.0}]
    ),
    num_rows="dynamic",
    use_container_width=True,
    key=f"editor_compra_{st.session_state.compra_version}",
    column_config={
        "tipo_peca": st.column_config.SelectboxColumn("Tipo de peça", options=nomes_tipo_peca),
        "preco_venda": st.column_config.NumberColumn("Preço de venda (R$)", min_value=0.0, step=1.0),
        "preco_custo": st.column_config.NumberColumn("Custo pago (R$)", min_value=0.0, step=1.0),
    },
)

itens_validos = itens[(itens["descricao"].str.strip() != "") & (itens["preco_venda"] > 0)]

valor_total = 0.0
if not itens_validos.empty:
    preview = itens_validos.copy()
    preview["margem (%)"] = (
        (preview["preco_venda"] - preview["preco_custo"]) / preview["preco_venda"] * 100
    ).round(1)
    st.dataframe(preview, use_container_width=True, hide_index=True)
    valor_total = round(itens_validos["preco_custo"].sum(), 2)
    st.metric("Valor total do lote (custo)", f"R$ {valor_total:.2f}")

if st.button("Registrar compra", type="primary", disabled=itens_validos.empty):
    tipo_peca_por_nome = {t["nome"]: t["id"] for t in tipos_peca}

    prazo = tipo_selecionado["prazo_dias"]
    if prazo > 0:
        data_vencimento = data_aceite + timedelta(days=prazo)
        status = "pendente"
        data_pagamento = None
    else:
        data_vencimento = None
        status = "pago"
        data_pagamento = data_aceite

    compra = run_query(
        """
        INSERT INTO compra
            (tipo_compra_id, fornecedora_id, data_aceite, valor_total,
             data_vencimento, status, data_pagamento)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        RETURNING id
        """,
        (
            tipo_selecionado["id"],
            fornecedora_id,
            data_aceite,
            valor_total,
            data_vencimento,
            status,
            data_pagamento,
        ),
    )
    compra_id = compra[0]["id"]

    for _, item in itens_validos.iterrows():
        produto = run_query(
            """
            INSERT INTO produto (compra_id, descricao, tipo_peca_id, tamanho, preco_custo, preco_venda)
            VALUES (%s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (
                compra_id,
                item["descricao"],
                tipo_peca_por_nome.get(item["tipo_peca"]),
                item["tamanho"] or None,
                item["preco_custo"],
                item["preco_venda"],
            ),
        )
        produto_id = produto[0]["id"]
        run_query(
            "INSERT INTO movimentacao_estoque (produto_id, tipo, observacao) "
            "VALUES (%s, 'entrada', %s)",
            (produto_id, f"Entrada via compra #{compra_id} ({nome_tipo_compra})"),
            fetch=False,
        )

    st.success(
        f"Compra #{compra_id} registrada com {len(itens_validos)} peça(s), "
        f"total R$ {valor_total:.2f}."
    )
    st.session_state.compra_version += 1
    st.rerun()

st.divider()
st.subheader("Compras recentes")

compras = run_query(
    """
    SELECT c.id, tc.nome AS tipo_compra, COALESCE(f.nome, '—') AS fornecedora,
           c.data_aceite, c.valor_total, c.data_vencimento, c.status, c.data_pagamento
    FROM compra c
    JOIN tipo_compra tc ON tc.id = c.tipo_compra_id
    LEFT JOIN fornecedora f ON f.id = c.fornecedora_id
    ORDER BY c.data_aceite DESC, c.id DESC
    LIMIT 30
    """
)

if compras:
    st.dataframe(compras, use_container_width=True, hide_index=True)

    pendentes = [c for c in compras if c["status"] == "pendente"]
    if pendentes:
        st.subheader("Marcar compra como paga")
        opcoes_pendentes = {
            f"Compra #{c['id']} — {c['fornecedora']} — R$ {c['valor_total']} "
            f"(vence {c['data_vencimento']})": c["id"]
            for c in pendentes
        }
        escolha = st.selectbox("Selecione a compra", options=list(opcoes_pendentes.keys()))
        if st.button("Marcar como paga"):
            run_query(
                "UPDATE compra SET status = 'pago', data_pagamento = CURRENT_DATE WHERE id = %s",
                (opcoes_pendentes[escolha],),
                fetch=False,
            )
            st.success("Compra marcada como paga.")
            st.rerun()
else:
    st.info("Nenhuma compra registrada ainda.")
