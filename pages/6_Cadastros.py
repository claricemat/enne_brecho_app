import streamlit as st

from db import run_query
from branding import aplicar_logo
from auth import exigir_login, botao_logout
from plano import (
    GRUPOS,
    carregar_contas,
    limpar_texto,
    rotulo_conta,
    subgrupo_existente,
    subgrupos_do_grupo,
)

st.set_page_config(page_title="Cadastros", page_icon="assets/icone_coracao.png", layout="wide")
aplicar_logo()
exigir_login()
botao_logout()
st.title("Cadastros")
st.caption("Catálogos usados nas outras telas do sistema.")

aba_peca, aba_compra, aba_contas, aba_contas_fin = st.tabs(
    ["Tipos de peça", "Tipos de compra", "Plano de contas", "Contas e caixa"]
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
# Plano de contas: Grupo > Subgrupo > Analítico
# ------------------------------------------------------------
with aba_contas:
    st.caption(
        "O plano de contas tem 3 níveis: Grupo › Subgrupo › Analítico. "
        "Ex.: Receita › Receita operacional › Venda de peças. "
        "As despesas e as vendas usam as contas analíticas; a conciliação bancária também."
    )
    NOVO_SUBGRUPO = "+ Novo subgrupo…"
    versao_plano = st.session_state.setdefault("plano_versao", 0)
    mensagem_plano = st.session_state.pop("plano_sucesso", None)
    if mensagem_plano:
        st.success(mensagem_plano)

    contas = carregar_contas()

    st.subheader("Nova conta")
    col1, col2, col3 = st.columns(3)
    grupo = col1.selectbox(
        "Grupo", options=list(GRUPOS), format_func=GRUPOS.get, key=f"plano_grupo_{versao_plano}"
    )
    subgrupos = subgrupos_do_grupo(contas, grupo)
    escolha_sub = col2.selectbox(
        "Subgrupo", options=subgrupos + [NOVO_SUBGRUPO], key=f"plano_sub_{versao_plano}_{grupo}"
    )
    if escolha_sub == NOVO_SUBGRUPO:
        subgrupo_digitado = col2.text_input(
            "Nome do novo subgrupo", key=f"plano_novo_sub_{versao_plano}_{grupo}"
        )
    else:
        subgrupo_digitado = escolha_sub
    nome_analitico = col3.text_input(
        "Analítico (nome da conta)", key=f"plano_nome_{versao_plano}",
        help="Ex.: Venda de peças, Aluguel, Compra de peças",
    )

    if st.button("Adicionar conta", type="primary", key=f"plano_add_{versao_plano}"):
        subgrupo_final = subgrupo_existente(contas, grupo, subgrupo_digitado)
        nome_final = limpar_texto(nome_analitico)
        if not subgrupo_final or not nome_final:
            st.error("Informe o subgrupo e o nome da conta (analítico).")
        else:
            try:
                run_query(
                    "INSERT INTO plano_contas (tipo, subgrupo, nome) VALUES (%s, %s, %s)",
                    (grupo, subgrupo_final, nome_final),
                    fetch=False,
                )
            except Exception as e:
                st.error(f"Não foi possível adicionar (talvez já exista nesse grupo). Erro: {e}")
            else:
                st.session_state["plano_sucesso"] = (
                    f"Conta adicionada: {GRUPOS[grupo]} › {subgrupo_final} › {nome_final}"
                )
                st.session_state["plano_versao"] = versao_plano + 1
                st.rerun()

    st.divider()
    st.subheader("Contas cadastradas")
    if contas:
        st.dataframe(
            [
                {"Grupo": GRUPOS.get(c["tipo"], c["tipo"]), "Subgrupo": c["subgrupo"], "Analítico": c["nome"]}
                for c in contas
            ],
            use_container_width=True,
            hide_index=True,
        )

        opcoes_conta = {rotulo_conta(c): c for c in contas}

        st.subheader("Editar conta")
        st.caption("Dá para mudar o subgrupo e o nome. O grupo não muda, porque despesas e vendas dependem dele.")
        escolha_edicao = st.selectbox("Conta", options=list(opcoes_conta), key="plano_editar_sel")
        conta_edicao = opcoes_conta[escolha_edicao]
        subgrupos_edicao = subgrupos_do_grupo(contas, conta_edicao["tipo"])
        col_a, col_b = st.columns(2)
        sub_escolhido = col_a.selectbox(
            "Subgrupo",
            options=subgrupos_edicao + [NOVO_SUBGRUPO],
            index=subgrupos_edicao.index(conta_edicao["subgrupo"]),
            key=f"plano_edit_sub_{conta_edicao['id']}",
        )
        if sub_escolhido == NOVO_SUBGRUPO:
            sub_edicao_digitado = col_a.text_input("Nome do novo subgrupo", key=f"plano_edit_novo_{conta_edicao['id']}")
        else:
            sub_edicao_digitado = sub_escolhido
        nome_edicao = col_b.text_input(
            "Analítico (nome da conta)", value=conta_edicao["nome"], key=f"plano_edit_nome_{conta_edicao['id']}"
        )
        if st.button("Salvar alterações", key="plano_edit_salvar"):
            sub_final = subgrupo_existente(contas, conta_edicao["tipo"], sub_edicao_digitado)
            nome_final = limpar_texto(nome_edicao)
            if not sub_final or not nome_final:
                st.error("Informe o subgrupo e o nome da conta.")
            else:
                try:
                    run_query(
                        "UPDATE plano_contas SET subgrupo = %s, nome = %s WHERE id = %s",
                        (sub_final, nome_final, conta_edicao["id"]),
                        fetch=False,
                    )
                except Exception as e:
                    st.error(f"Não foi possível salvar (talvez já exista uma conta com esse nome). Erro: {e}")
                else:
                    st.session_state["plano_sucesso"] = "Conta atualizada."
                    st.rerun()

        st.subheader("Excluir conta")
        escolha_exclusao = st.selectbox("Conta", options=list(opcoes_conta), key="excluir_conta")
        if st.button("Excluir conta", type="secondary", key="plano_excluir_conta"):
            try:
                run_query(
                    "DELETE FROM plano_contas WHERE id = %s", (opcoes_conta[escolha_exclusao]["id"],), fetch=False
                )
            except Exception as e:
                st.error(
                    "Não foi possível excluir — provavelmente já existe despesa, venda ou "
                    f"lançamento do extrato usando essa conta. Erro original: {e}"
                )
            else:
                st.session_state["plano_sucesso"] = "Conta excluída."
                st.rerun()
    else:
        st.info("Nenhuma conta cadastrada ainda.")

# ------------------------------------------------------------
# Contas bancárias e caixa (usadas nos pagamentos de compras)
# ------------------------------------------------------------
with aba_contas_fin:
    st.caption(
        "Contas bancárias e caixa de onde sai o dinheiro dos pagamentos. "
        "Use um nome que identifique a conta e de quem é (ex: Nubank Ana, Itaú Clarice)."
    )
    with st.form("nova_conta_financeira", clear_on_submit=True):
        col1, col2 = st.columns(2)
        nome = col1.text_input("Nome da conta")
        tipo_rotulo = col2.selectbox("Tipo", ["Conta bancária", "Caixa"])
        if st.form_submit_button("Adicionar"):
            if not nome.strip():
                st.error("Informe um nome.")
            else:
                try:
                    run_query(
                        "INSERT INTO conta_financeira (nome, tipo) VALUES (%s, %s)",
                        (nome.strip(), "banco" if tipo_rotulo == "Conta bancária" else "caixa"),
                        fetch=False,
                    )
                    st.success(f"'{nome}' adicionada.")
                    st.rerun()
                except Exception as e:
                    st.error(f"Não foi possível adicionar (talvez já exista). Erro: {e}")

    contas_fin = run_query(
        """
        SELECT id, nome,
               CASE tipo WHEN 'banco' THEN 'Conta bancária' ELSE 'Caixa' END AS tipo_conta
        FROM conta_financeira
        ORDER BY conta_financeira.tipo, nome
        """
    )
    if contas_fin:
        st.dataframe(contas_fin, use_container_width=True, hide_index=True)
        opcoes = {c["nome"]: c["id"] for c in contas_fin}
        escolha = st.selectbox("Excluir", options=list(opcoes.keys()), key="excluir_conta_fin")
        if st.button("Excluir conta", type="secondary", key="conta_fin_excluir"):
            try:
                run_query(
                    "DELETE FROM conta_financeira WHERE id = %s", (opcoes[escolha],), fetch=False
                )
                st.success("Excluída.")
                st.rerun()
            except Exception as e:
                st.error(
                    "Não foi possível excluir — provavelmente já existe pagamento "
                    f"registrado com essa conta. Erro original: {e}"
                )
    else:
        st.info("Nenhuma conta cadastrada ainda.")
