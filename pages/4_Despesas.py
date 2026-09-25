import streamlit as st

from auth import botao_logout, exigir_login
from branding import aplicar_logo
from db import run_query, transacao
from formatacao import brl, fmt_data, hoje_brasil
from parcelas import dec, editor_parcelas

st.set_page_config(page_title="Despesas", page_icon="assets/icone_coracao.png", layout="wide")
aplicar_logo()
exigir_login()
botao_logout()
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
hoje = hoje_brasil()
versao = st.session_state.setdefault("despesa_versao", 0)
usuario = st.session_state.get("usuario")

mensagem = st.session_state.pop("despesa_sucesso", None)
if mensagem:
    st.success(mensagem)


def _rotulo_parcela(d):
    return f"{d['parcela_numero']}/{d['parcela_total']}" if d["parcelamento_id"] else "—"


# ------------------------------------------------------------
# Registrar despesa (à vista ou parcelada)
# ------------------------------------------------------------
st.subheader("Registrar despesa")
col1, col2 = st.columns(2)
nome_categoria = col1.selectbox("Categoria*", options=list(opcoes_conta), key=f"desp_cat_{versao}")
valor = col2.number_input(
    "Valor total (R$)*", min_value=0.0, step=1.0, format="%.2f", key=f"desp_valor_{versao}"
)
descricao = st.text_input("Descrição (opcional)", key=f"desp_desc_{versao}")
parcelar = st.checkbox(
    "Parcelar esta despesa", key=f"desp_parcelar_{versao}",
    help="Divide o valor em parcelas com vencimentos diferentes. Cada parcela vira um "
         "lançamento (1/3, 2/3, 3/3), pago separadamente.",
)

parcelas, erro = [], None
if not parcelar:
    col3, col4 = st.columns(2)
    data_despesa = col3.date_input("Data*", value=hoje, format="DD/MM/YYYY", key=f"desp_data_{versao}")
    status_pagamento = col4.selectbox("Status", ["pago", "pendente"], key=f"desp_status_{versao}")
elif valor <= 0:
    st.info("Informe o valor total para montar as parcelas.")
else:
    data_base = st.date_input(
        "Data da despesa", value=hoje, format="DD/MM/YYYY", key=f"desp_data_base_{versao}",
        help="Nenhuma parcela pode vencer antes desta data.",
    )
    parcelas, erro = editor_parcelas(
        valor, data_base, chave=f"desp_parc_{versao}", quantidade_padrao=2,
        vencimento_padrao=data_base,
    )
    if erro:
        st.error(erro)
    elif len(parcelas) < 2:
        erro = "Para parcelar, use 2 parcelas ou mais (ou desmarque \"Parcelar esta despesa\")."
        st.error(erro)
    primeira_paga = st.checkbox(
        "A 1ª parcela já foi paga", key=f"desp_primeira_paga_{versao}",
        help="As demais entram como pendentes; marque cada uma como paga quando pagar.",
    )

if st.button("Registrar", type="primary", key=f"desp_registrar_{versao}", disabled=parcelar and bool(erro or not parcelas)):
    if valor <= 0:
        st.error("Informe um valor maior que zero.")
    elif not parcelar:
        run_query(
            """
            INSERT INTO despesa (plano_conta_id, descricao, valor, data, status_pagamento)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (opcoes_conta[nome_categoria], descricao.strip() or None, valor, data_despesa, status_pagamento),
            fetch=False,
        )
        st.session_state["despesa_sucesso"] = f"Despesa de {brl(valor)} registrada."
        st.session_state["despesa_versao"] = versao + 1
        st.rerun()
    else:
        try:
            with transacao() as executar:
                parcelamento = executar(
                    "INSERT INTO despesa_parcelamento (descricao, valor_total, parcelas, criado_por) "
                    "VALUES (%s, %s, %s, %s) RETURNING id",
                    (descricao.strip() or None, dec(valor), len(parcelas), usuario),
                    fetch=True,
                )[0]["id"]
                for p in parcelas:
                    paga = primeira_paga and p["numero"] == 1
                    executar(
                        """
                        INSERT INTO despesa
                            (plano_conta_id, descricao, valor, data, status_pagamento,
                             parcelamento_id, parcela_numero, parcela_total)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                        """,
                        (
                            opcoes_conta[nome_categoria], descricao.strip() or None, p["valor"],
                            p["data_vencimento"], "pago" if paga else "pendente",
                            parcelamento, p["numero"], len(parcelas),
                        ),
                    )
        except Exception as e:
            st.error(f"Não foi possível registrar. Nada foi gravado. Erro: {e}")
        else:
            st.session_state["despesa_sucesso"] = (
                f"Despesa de {brl(valor)} registrada em {len(parcelas)} parcelas "
                f"(1ª vence em {fmt_data(parcelas[0]['data_vencimento'])})."
            )
            st.session_state["despesa_versao"] = versao + 1
            st.rerun()

st.divider()

# ------------------------------------------------------------
# Resumo
# ------------------------------------------------------------
resumo = run_query(
    """
    SELECT
        COALESCE(SUM(valor) FILTER (
            WHERE date_trunc('month', data) = date_trunc('month', %s::date)
        ), 0) AS total_mes,
        COALESCE(SUM(valor) FILTER (WHERE status_pagamento = 'pendente'), 0) AS total_pendente,
        COALESCE(SUM(valor) FILTER (WHERE status_pagamento = 'pendente' AND data < %s), 0) AS total_atrasado
    FROM despesa
    """,
    (hoje, hoje),
)[0]

col1, col2, col3 = st.columns(3)
col1.metric("Despesas este mês", brl(resumo["total_mes"]), help="Lançamentos com data neste mês (parcelas pela data de vencimento).")
col2.metric("Pendentes (a pagar)", brl(resumo["total_pendente"]), help="Inclui parcelas futuras.")
col3.metric("Pendentes vencidas", brl(resumo["total_atrasado"]))

# ------------------------------------------------------------
# Marcar como paga (todas as pendentes, da mais antiga para a mais nova)
# ------------------------------------------------------------
pendentes = run_query(
    """
    SELECT d.id, d.data, pc.nome AS categoria, d.descricao, d.valor,
           d.parcelamento_id, d.parcela_numero, d.parcela_total
    FROM despesa d
    JOIN plano_contas pc ON pc.id = d.plano_conta_id
    WHERE d.status_pagamento = 'pendente'
    ORDER BY d.data, d.id
    """
)
if pendentes:
    st.subheader("Marcar como paga")
    opcoes_pendentes = {}
    for d in pendentes:
        rotulo = f"#{d['id']} — {fmt_data(d['data'])} — {d['categoria']}"
        if d["descricao"]:
            rotulo += f" ({d['descricao']})"
        if d["parcelamento_id"]:
            rotulo += f" — parcela {_rotulo_parcela(d)}"
        rotulo += f" — {brl(d['valor'])}"
        if d["data"] < hoje:
            rotulo += " — vencida"
        opcoes_pendentes[rotulo] = d["id"]
    escolhidas = st.multiselect(
        "Despesas pagas", options=list(opcoes_pendentes), key=f"desp_pagar_{versao}",
        placeholder="Escolha uma ou mais",
    )
    if st.button("Marcar como paga(s)", disabled=not escolhidas, key="desp_btn_pagar"):
        ids = [opcoes_pendentes[r] for r in escolhidas]
        feitas = run_query(
            "UPDATE despesa SET status_pagamento = 'pago' WHERE id = ANY(%s) AND status_pagamento = 'pendente' RETURNING id",
            (ids,),
        )
        st.session_state["despesa_sucesso"] = f"{len(feitas)} despesa(s) marcada(s) como paga(s)."
        st.session_state["despesa_versao"] = versao + 1
        st.rerun()

# ------------------------------------------------------------
# Lançamentos recentes + exclusão
# ------------------------------------------------------------
st.subheader("Despesas registradas recentemente")
despesas = run_query(
    """
    SELECT d.id, d.data, pc.nome AS categoria, d.descricao, d.valor, d.status_pagamento,
           d.parcelamento_id, d.parcela_numero, d.parcela_total
    FROM despesa d
    JOIN plano_contas pc ON pc.id = d.plano_conta_id
    ORDER BY d.id DESC LIMIT 40
    """
)

if despesas:
    st.dataframe(
        [
            {
                "Nº": d["id"],
                "Data / vencimento": fmt_data(d["data"]),
                "Categoria": d["categoria"],
                "Descrição": d["descricao"] or "",
                "Parcela": _rotulo_parcela(d),
                "Valor": brl(d["valor"]),
                "Status": d["status_pagamento"],
            }
            for d in despesas
        ],
        use_container_width=True,
        hide_index=True,
    )

    st.subheader("Excluir despesa")
    st.caption("Para corrigir um lançamento errado.")
    opcoes_todas = {
        f"#{d['id']} — {fmt_data(d['data'])} — {d['categoria']}"
        + (f" — parcela {_rotulo_parcela(d)}" if d["parcelamento_id"] else "")
        + f" — {brl(d['valor'])}": d
        for d in despesas
    }
    escolha_excluir = st.selectbox("Selecione", options=list(opcoes_todas), key="excluir_despesa")
    despesa_excluir = opcoes_todas[escolha_excluir]
    excluir_tudo = False
    if despesa_excluir["parcelamento_id"]:
        excluir_tudo = (
            st.radio(
                "O que excluir",
                ["Só esta parcela", f"O parcelamento inteiro ({despesa_excluir['parcela_total']} parcelas, inclusive as pagas)"],
                key=f"excluir_desp_modo_{despesa_excluir['id']}",
            )
            != "Só esta parcela"
        )
    if st.button("Excluir", type="secondary", key="desp_btn_excluir"):
        if excluir_tudo:
            run_query(
                "DELETE FROM despesa_parcelamento WHERE id = %s",
                (despesa_excluir["parcelamento_id"],),
                fetch=False,
            )
            st.session_state["despesa_sucesso"] = "Parcelamento excluído (todas as parcelas)."
        else:
            run_query("DELETE FROM despesa WHERE id = %s", (despesa_excluir["id"],), fetch=False)
            st.session_state["despesa_sucesso"] = "Despesa excluída."
        st.rerun()
else:
    st.info("Nenhuma despesa registrada ainda.")
