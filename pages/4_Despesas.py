from datetime import date

import streamlit as st

from db import run_query
from branding import aplicar_logo

st.set_page_config(page_title="Despesas", page_icon="assets/icone_coracao.png", layout="wide")
aplicar_logo()
st.title("Despesas")

contas_despesa = run_query(
    "SELECT id, nome FROM plano_contas WHERE tipo = 'despesa' ORDER BY nome"
)
if not contas_despesa:
    st.warning(
        "Nenhuma categoria de despesa cadastrada. Vá em Cadastros → Plano de contas e crie uma."
    )
    st.stop()

opcoes_conta = {c["nome"]: c["id"] for c in contas_despesa}

with st.form("nova_despesa", clear_on_submit=True):
    st.subheader("Registrar despesa")
    col1, col2 = st.columns(2)
    nome_categoria = col1.selectbox("Categoria*", options=list(opcoes_conta.keys()))
    valor = col2.number_input("Valor (R$)*", min_value=0.0, step=1.0)
    col3, col4 = st.columns(2)
    data_despesa = col3.date_input("Data*", value=date.today())
    status_pagamento = col4.selectbox("Status", ["pago", "pendente"])
    descricao = st.text_input("Descrição (opcional)")
    enviado = st.form_submit_button("Registrar")

    if enviado:
        if valor <= 0:
            st.error("Informe um valor maior que zero.")
        else:
            run_query(
                """
                INSERT INTO despesa (plano_conta_id, descricao, valor, data, status_pagamento)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (
                    opcoes_conta[nome_categoria],
                    descricao.strip() or None,
                    valor,
                    data_despesa,
                    status_pagamento,
                ),
                fetch=False,
            )
            st.success(f"Despesa de R$ {valor:.2f} registrada.")
            st.rerun()

st.divider()

resumo = run_query(
    """
    SELECT
        COALESCE(SUM(valor) FILTER (
            WHERE date_trunc('month', data) = date_trunc('month', CURRENT_DATE)
        ), 0) AS total_mes,
        COALESCE(SUM(valor) FILTER (WHERE status_pagamento = 'pendente'), 0) AS total_pendente
    FROM despesa
    """
)[0]

col1, col2 = st.columns(2)
col1.metric("Despesas este mês", f"R$ {resumo['total_mes']:.2f}")
col2.metric("Pendentes (a pagar)", f"R$ {resumo['total_pendente']:.2f}")

st.subheader("Despesas recentes")
despesas = run_query(
    """
    SELECT d.id, d.data, pc.nome AS categoria, d.descricao, d.valor, d.status_pagamento
    FROM despesa d
    JOIN plano_contas pc ON pc.id = d.plano_conta_id
    ORDER BY d.data DESC, d.id DESC LIMIT 30
    """
)

if despesas:
    st.dataframe(despesas, use_container_width=True, hide_index=True)

    pendentes = [d for d in despesas if d["status_pagamento"] == "pendente"]
    if pendentes:
        st.subheader("Marcar despesa como paga")
        opcoes_pendentes = {
            f"#{d['id']} — {d['categoria']} — R$ {d['valor']} ({d['data']})": d["id"]
            for d in pendentes
        }
        escolha = st.selectbox("Selecione a despesa", options=list(opcoes_pendentes.keys()))
        if st.button("Marcar como paga"):
            run_query(
                "UPDATE despesa SET status_pagamento = 'pago' WHERE id = %s",
                (opcoes_pendentes[escolha],),
                fetch=False,
            )
            st.success("Despesa marcada como paga.")
            st.rerun()

    st.subheader("Excluir despesa")
    st.caption("Para corrigir um lançamento errado.")
    opcoes_todas = {
        f"#{d['id']} — {d['categoria']} — R$ {d['valor']} ({d['data']})": d["id"]
        for d in despesas
    }
    escolha_excluir = st.selectbox(
        "Selecione", options=list(opcoes_todas.keys()), key="excluir_despesa"
    )
    if st.button("Excluir", type="secondary"):
        run_query(
            "DELETE FROM despesa WHERE id = %s", (opcoes_todas[escolha_excluir],), fetch=False
        )
        st.success("Despesa excluída.")
        st.rerun()
else:
    st.info("Nenhuma despesa registrada ainda.")
