import streamlit as st
from db import run_query
from branding import aplicar_logo

st.set_page_config(page_title="Fornecedoras", page_icon="assets/icone_coracao.png", layout="wide")
aplicar_logo()
st.title("Fornecedoras")

with st.form("nova_fornecedora", clear_on_submit=True):
    st.subheader("Cadastrar nova fornecedora")
    nome = st.text_input("Nome*")
    contato = st.text_input("Contato (telefone, Instagram etc.)")
    enviado = st.form_submit_button("Cadastrar")

    if enviado:
        if not nome.strip():
            st.error("O nome é obrigatório.")
        else:
            run_query(
                "INSERT INTO fornecedora (nome, contato) VALUES (%s, %s)",
                (nome.strip(), contato.strip() or None),
                fetch=False,
            )
            st.success(f"Fornecedora '{nome}' cadastrada.")
            st.rerun()

st.divider()
st.subheader("Fornecedoras cadastradas")

fornecedoras = run_query(
    "SELECT id, nome, contato, criado_em FROM fornecedora ORDER BY nome"
)

if fornecedoras:
    st.dataframe(fornecedoras, use_container_width=True, hide_index=True)

    st.subheader("Excluir fornecedora")
    st.caption("Só é possível excluir fornecedoras que ainda não têm nenhuma compra registrada.")
    opcoes = {f"{f['nome']} (id {f['id']})": f["id"] for f in fornecedoras}
    escolha = st.selectbox("Selecione", options=list(opcoes.keys()))
    if st.button("Excluir", type="secondary"):
        try:
            run_query(
                "DELETE FROM fornecedora WHERE id = %s", (opcoes[escolha],), fetch=False
            )
            st.success("Fornecedora excluída.")
            st.rerun()
        except Exception as e:
            st.error(
                "Não foi possível excluir — essa fornecedora provavelmente já tem "
                "compras registradas. Erro original: " + str(e)
            )
else:
    st.info("Nenhuma fornecedora cadastrada ainda.")
