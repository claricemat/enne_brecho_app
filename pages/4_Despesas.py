import streamlit as st

from auth import botao_logout, exigir_login
from branding import aplicar_logo
from db import run_query, transacao
from formatacao import brl, fmt_data, hoje_brasil
from pagamento_parcial import PagamentoParcialInvalidoError, pagar_despesa_parcial
from parcelas import dec, editor_parcelas
from plano import GRUPOS, carregar_contas

st.set_page_config(page_title="Despesas", page_icon="assets/icone_coracao.png", layout="wide")
aplicar_logo()
exigir_login()
botao_logout()
st.title("Despesas")

# contas analíticas do plano de contas que podem receber despesas (grupos Despesa e Custo)
contas_despesa = [c for c in carregar_contas() if c["tipo"] in ("despesa", "custo")]
if not contas_despesa:
    st.warning(
        "Nenhuma conta de despesa no plano de contas. Vá em Cadastros → Plano de contas e crie uma "
        "(grupo Despesa ou Custo)."
    )
    st.stop()

opcoes_conta = {
    f"{c['nome']}  ({GRUPOS[c['tipo']]} › {c['subgrupo']})": c["id"] for c in contas_despesa
}

# de onde sai o dinheiro: contas bancárias e caixa (Cadastros → Contas e caixa)
contas_pagamento = run_query("SELECT id, nome, tipo FROM conta_financeira ORDER BY tipo DESC, nome")
opcoes_pagamento = {
    f"{c['nome']} ({'caixa' if c['tipo'] == 'caixa' else 'conta bancária'})": c["id"]
    for c in contas_pagamento
}
hoje = hoje_brasil()
versao = st.session_state.setdefault("despesa_versao", 0)
usuario = st.session_state.get("usuario")

mensagem = st.session_state.pop("despesa_sucesso", None)
if mensagem:
    st.success(mensagem)


def _rotulo_parcela(d):
    return f"{d['parcela_numero']}/{d['parcela_total']}" if d["parcelamento_id"] else "—"


def _rotulo_plano(d):
    return f"{d['subgrupo']} › {d['categoria']}"


def _campos_pagamento(chave, data_padrao, rotulo_conta="Paga com (conta ou caixa)*"):
    """Conta/caixa de onde saiu o dinheiro + data do pagamento. Retorna (conta_id, data, erro)."""
    if not opcoes_pagamento:
        erro = "Cadastre uma conta bancária ou o caixa em Cadastros → Contas e caixa para registrar pagamentos."
        st.warning(erro)
        return None, None, erro
    c1, c2 = st.columns(2)
    escolha = c1.selectbox(rotulo_conta, list(opcoes_pagamento), index=None,
                           placeholder="De onde saiu o dinheiro?", key=f"{chave}_conta")
    data_pag = c2.date_input("Data do pagamento*", value=min(data_padrao, hoje), max_value=hoje,
                             format="DD/MM/YYYY", key=f"{chave}_datapag")
    if escolha is None:
        return None, data_pag, "Informe a conta ou o caixa de onde saiu o pagamento."
    return opcoes_pagamento[escolha], data_pag, None


# ------------------------------------------------------------
# Registrar despesa (à vista ou parcelada)
# ------------------------------------------------------------
st.subheader("Registrar despesa")
col1, col2 = st.columns(2)
nome_categoria = col1.selectbox(
    "Conta (plano de contas)*", options=list(opcoes_conta), index=None,
    placeholder="Ex.: Aluguel, Energia, Embalagens…", key=f"desp_cat_{versao}",
    help="Contas analíticas dos grupos Despesa e Custo do plano de contas (Cadastros → Plano de contas).",
)
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
conta_pag, data_pag, erro_pag = None, None, None
primeira_paga = False
if not parcelar:
    col3, col4 = st.columns(2)
    data_despesa = col3.date_input("Data / vencimento*", value=hoje, format="DD/MM/YYYY", key=f"desp_data_{versao}")
    status_pagamento = col4.selectbox("Status", ["pago", "pendente"], key=f"desp_status_{versao}")
    if status_pagamento == "pago":
        conta_pag, data_pag, erro_pag = _campos_pagamento(f"desp_pag_{versao}", data_despesa)
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
    if primeira_paga and parcelas:
        conta_pag, data_pag, erro_pag = _campos_pagamento(
            f"desp_pag1_{versao}", parcelas[0]["data_vencimento"], "1ª parcela paga com (conta ou caixa)*"
        )

if erro_pag and (status_pagamento == "pago" if not parcelar else primeira_paga):
    st.caption(f"⚠ {erro_pag}")
if st.button(
    "Registrar", type="primary", key=f"desp_registrar_{versao}",
    disabled=(parcelar and bool(erro or not parcelas)) or nome_categoria is None or bool(erro_pag),
):
    if valor <= 0:
        st.error("Informe um valor maior que zero.")
    elif not parcelar:
        pago = status_pagamento == "pago"
        run_query(
            """
            INSERT INTO despesa (plano_conta_id, descricao, valor, data, status_pagamento,
                                 conta_financeira_id, data_pagamento)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (opcoes_conta[nome_categoria], descricao.strip() or None, valor, data_despesa, status_pagamento,
             conta_pag if pago else None, data_pag if pago else None),
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
                             parcelamento_id, parcela_numero, parcela_total,
                             conta_financeira_id, data_pagamento)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        """,
                        (
                            opcoes_conta[nome_categoria], descricao.strip() or None, p["valor"],
                            p["data_vencimento"], "pago" if paga else "pendente",
                            parcelamento, p["numero"], len(parcelas),
                            conta_pag if paga else None, data_pag if paga else None,
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
    SELECT d.id, d.data, pc.nome AS categoria, pc.subgrupo, d.descricao, d.valor,
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
        rotulo = f"#{d['id']} — {fmt_data(d['data'])} — {_rotulo_plano(d)}"
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
    conta_marcar, data_marcar, erro_marcar = (None, None, None)
    if escolhidas:
        conta_marcar, data_marcar, erro_marcar = _campos_pagamento(f"desp_marcar_{versao}", hoje)
    if st.button("Marcar como paga(s)", disabled=not escolhidas or bool(erro_marcar), key="desp_btn_pagar"):
        ids = [opcoes_pendentes[r] for r in escolhidas]
        feitas = run_query(
            "UPDATE despesa SET status_pagamento = 'pago', conta_financeira_id = %s, data_pagamento = %s "
            "WHERE id = ANY(%s) AND status_pagamento = 'pendente' RETURNING id",
            (conta_marcar, data_marcar, ids),
        )
        st.session_state["despesa_sucesso"] = f"{len(feitas)} despesa(s) marcada(s) como paga(s)."
        st.session_state["despesa_versao"] = versao + 1
        st.rerun()

    # ---- pagamento parcial: paga uma parte agora, o restante continua em aberto ----
    with st.expander("Pagar só uma parte de uma despesa"):
        st.caption(
            "Ex.: aluguel de R$ 400,00 — pagou R$ 250,00 agora e o restante depois. A parte paga "
            "e o restante continuam com o mesmo vencimento; mudar o vencimento do restante é opcional."
        )
        por_rotulo = {r: next(d for d in pendentes if d["id"] == i) for r, i in opcoes_pendentes.items()}
        escolha_parcial = st.selectbox(
            "Despesa", list(por_rotulo), index=None, placeholder="Escolha a despesa",
            key=f"desp_parcial_sel_{versao}",
        )
        if escolha_parcial:
            alvo = por_rotulo[escolha_parcial]
            valor_total_desp = float(alvo["valor"])
            if valor_total_desp < 0.02:
                st.info("Essa despesa é pequena demais para dividir.")
            else:
                valor_pago = st.number_input(
                    "Valor pago agora (R$)", min_value=0.01, max_value=round(valor_total_desp - 0.01, 2),
                    value=round(valor_total_desp / 2, 2), step=1.0, format="%.2f",
                    key=f"desp_parcial_valor_{versao}_{alvo['id']}",
                )
                mudar_venc = st.checkbox(
                    "Mudar o vencimento do restante", key=f"desp_parcial_mudar_{versao}_{alvo['id']}"
                )
                venc_restante = None
                if mudar_venc:
                    venc_restante = st.date_input(
                        "O restante vence em", value=alvo["data"], format="DD/MM/YYYY",
                        key=f"desp_parcial_venc_{versao}_{alvo['id']}",
                    )
                restante = dec(valor_total_desp) - dec(valor_pago)
                venc_txt = (
                    f"vencendo em {fmt_data(venc_restante)}" if venc_restante
                    else f"com o mesmo vencimento ({fmt_data(alvo['data'])})"
                )
                conta_parc, data_parc, erro_parc = _campos_pagamento(f"desp_parcial_pag_{versao}_{alvo['id']}", hoje)
                st.caption(f"Fica: {brl(valor_pago)} pago e {brl(restante)} em aberto, {venc_txt}.")
                if st.button("Registrar pagamento parcial", type="primary", key=f"desp_parcial_btn_{versao}",
                             disabled=bool(erro_parc)):
                    try:
                        pagar_despesa_parcial(alvo["id"], alvo["valor"], valor_pago, venc_restante,
                                              conta_parc, data_parc)
                    except PagamentoParcialInvalidoError as e:
                        st.error(str(e))
                    else:
                        st.session_state["despesa_sucesso"] = (
                            f"Pagamento parcial registrado: {brl(valor_pago)} pago; "
                            f"restam {brl(restante)} em aberto, {venc_txt}."
                        )
                        st.session_state["despesa_versao"] = versao + 1
                        st.rerun()

# ------------------------------------------------------------
# Lançamentos recentes + exclusão
# ------------------------------------------------------------
st.subheader("Despesas registradas recentemente")
despesas = run_query(
    """
    SELECT d.id, d.data, pc.nome AS categoria, pc.subgrupo, d.descricao, d.valor, d.status_pagamento,
           d.parcelamento_id, d.parcela_numero, d.parcela_total, d.data_pagamento,
           cf.nome AS conta_pagamento
    FROM despesa d
    JOIN plano_contas pc ON pc.id = d.plano_conta_id
    LEFT JOIN conta_financeira cf ON cf.id = d.conta_financeira_id
    ORDER BY d.id DESC LIMIT 40
    """
)

if despesas:
    st.dataframe(
        [
            {
                "Nº": d["id"],
                "Data / vencimento": fmt_data(d["data"]),
                "Conta (plano de contas)": _rotulo_plano(d),
                "Descrição": d["descricao"] or "",
                "Parcela": _rotulo_parcela(d),
                "Valor": brl(d["valor"]),
                "Status": d["status_pagamento"],
                "Paga com": (d["conta_pagamento"] or "não informado") if d["status_pagamento"] == "pago" else "—",
                "Paga em": (fmt_data(d["data_pagamento"]) if d["data_pagamento"] else "não informado")
                           if d["status_pagamento"] == "pago" else "—",
            }
            for d in despesas
        ],
        use_container_width=True,
        hide_index=True,
    )

    st.subheader("Excluir despesa")
    st.caption("Para corrigir um lançamento errado.")
    opcoes_todas = {
        f"#{d['id']} — {fmt_data(d['data'])} — {_rotulo_plano(d)}"
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
