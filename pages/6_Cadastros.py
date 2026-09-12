import streamlit as st

from db import run_query
from branding import aplicar_logo

st.set_page_config(page_title="Cadastros", page_icon="assets/icone_coracao.png", layout="wide")
aplicar_logo()
st.title("Cadastros")
st.caption("Catálogos usados nas outras telas do sistema.")

aba_peca, aba_compra, aba_contas = st.tabs(
    ["Tipos de peça", "Tipos de compra", "Plano de contas"]
)

# ------------------------------------------------------------
# Tipos de peça
# ------------------------------------------------------------
with aba_peca:
    with st.form("novo_tipo_peca", clear_on_submit=True):
        nome = st.text_input("Nome do tipo de peça (ex: Vestidos, Bolsas)")
        if st.form_submit_button("Adicionar"):
            if not nome.strip():
                st.error("Informe um nome.")
            else:
                try:
                    run_query(
                        "INSERT INTO tipo_peca (nome) VALUES (%s)", (nome.strip(),), fetch=False
                    )
                    st.success(f"'{nome}' adicionado.")
                    st.rerun()
                except Exception as e:
                    st.error(f"Não foi possível adicionar (talvez já exista). Erro: {e}")

    tipos_peca = run_query("SELECT id, nome FROM tipo_peca ORDER BY nome")
    if tipos_peca:
        st.dataframe(tipos_peca, use_container_width=True, hide_index=True)
        opcoes = {t["nome"]: t["id"] for t in tipos_peca}
        escolha = st.selectbox("Excluir", options=list(opcoes.keys()), key="excluir_tipo_peca")
        if st.button("Excluir tipo de peça", type="secondary"):
            try:
                run_query(
                    "DELETE FROM tipo_peca WHERE id = %s", (opcoes[escolha],), fetch=False
                )
                st.success("Excluído.")
                st.rerun()
            except Exception as e:
                st.error(
                    "Não foi possível excluir — provavelmente já existe peça cadastrada "
                    f"com esse tipo. Erro original: {e}"
                )
    else:
        st.info("Nenhum tipo de peça cadastrado ainda.")

# ------------------------------------------------------------
# Tipos de compra
# ------------------------------------------------------------
with aba_compra:
    with st.form("novo_tipo_compra", clear_on_submit=True):
        nome = st.text_input("Nome do tipo de compra (ex: Doação, Consignação 60%)")
        col1, col2 = st.columns(2)
        prazo_dias = col1.number_input(
            "Prazo de pagamento (dias)", min_value=0, step=1, value=0,
            help="0 = pago na hora (à vista)",
        )
        requer_fornecedora = col2.checkbox("Exige selecionar uma fornecedora", value=True)
        if st.form_submit_button("Adicionar"):
            if not nome.strip():
                st.error("Informe um nome.")
            else:
                try:
                    run_query(
                        "INSERT INTO tipo_compra (nome, prazo_dias, requer_fornecedora) "
                        "VALUES (%s, %s, %s)",
                        (nome.strip(), prazo_dias, requer_fornecedora),
                        fetch=False,
                    )
                    st.success(f"'{nome}' adicionado.")
                    st.rerun()
                except Exception as e:
                    st.error(f"Não foi possível adicionar (talvez já exista). Erro: {e}")

    tipos_compra = run_query(
        "SELECT id, nome, prazo_dias, requer_fornecedora FROM tipo_compra ORDER BY id"
    )
    if tipos_compra:
        st.dataframe(tipos_compra, use_container_width=True, hide_index=True)
        opcoes = {t["nome"]: t["id"] for t in tipos_compra}
        escolha = st.selectbox("Excluir", options=list(opcoes.keys()), key="excluir_tipo_compra")
        if st.button("Excluir tipo de compra", type="secondary"):
            try:
                run_query(
                    "DELETE FROM tipo_compra WHERE id = %s", (opcoes[escolha],), fetch=False
                )
                st.success("Excluído.")
                st.rerun()
            except Exception as e:
                st.error(
                    "Não foi possível excluir — provavelmente já existe compra registrada "
                    f"com esse tipo. Erro original: {e}"
                )
    else:
        st.info("Nenhum tipo de compra cadastrado ainda.")

# ------------------------------------------------------------
# Plano de contas (despesas e receitas)
# ------------------------------------------------------------
with aba_contas:
    with st.form("nova_conta", clear_on_submit=True):
        col1, col2 = st.columns(2)
        tipo_conta = col1.selectbox("Tipo", ["despesa", "receita"])
        nome = col2.text_input("Nome (ex: Fretes, Comissões)")
        if st.form_submit_button("Adicionar"):
            if not nome.strip():
                st.error("Informe um nome.")
            else:
                try:
                    run_query(
                        "INSERT INTO plano_contas (tipo, nome) VALUES (%s, %s)",
                        (tipo_conta, nome.strip()),
                        fetch=False,
                    )
                    st.success(f"'{nome}' adicionado em {tipo_conta}.")
                    st.rerun()
                except Exception as e:
                    st.error(f"Não foi possível adicionar (talvez já exista). Erro: {e}")

    contas = run_query("SELECT id, tipo, nome FROM plano_contas ORDER BY tipo, nome")
    if contas:
        st.dataframe(contas, use_container_width=True, hide_index=True)
        opcoes = {f"[{c['tipo']}] {c['nome']}": c["id"] for c in contas}
        escolha = st.selectbox("Excluir", options=list(opcoes.keys()), key="excluir_conta")
        if st.button("Excluir conta", type="secondary"):
            try:
                run_query(
                    "DELETE FROM plano_contas WHERE id = %s", (opcoes[escolha],), fetch=False
                )
                st.success("Excluído.")
                st.rerun()
            except Exception as e:
                st.error(
                    "Não foi possível excluir — provavelmente já existe despesa ou venda "
                    f"usando essa conta. Erro original: {e}"
                )
    else:
        st.info("Nenhuma conta cadastrada ainda.")
