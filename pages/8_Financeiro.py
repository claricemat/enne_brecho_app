from datetime import timedelta
from decimal import Decimal

import pandas as pd
import streamlit as st

from auth import exigir_login, botao_logout
from branding import aplicar_logo
from financeiro import (
    carregar_extrato,
    cobertura_por_conta,
    conciliar,
    contas_bancarias,
    desfazer_conciliacao,
    fitids_ja_importados,
    importar_lancamentos,
    normalizar_descricao,
    resumo_periodo,
    resumo_por_plano,
    sugestoes_por_descricao,
    ultima_data_extrato,
)
from formatacao import brl, fmt_data, hoje_brasil
from ofx_parser import OfxInvalido, ler_ofx, mesma_conta
from plano import GRUPOS, carregar_contas, rotulo_conta

st.set_page_config(page_title="Financeiro", page_icon="assets/icone_coracao.png", layout="wide")
aplicar_logo()
exigir_login()
botao_logout()
st.title("Financeiro")
st.caption(
    "Importe o extrato bancário (OFX) e concilie cada lançamento com uma conta do plano de contas."
)

LIMITE_LINHAS = 500
hoje = hoje_brasil()
usuario = st.session_state.get("usuario")
st.session_state.setdefault("fin_versao", 0)

bancos = contas_bancarias()
plano = carregar_contas()

mensagem = st.session_state.pop("fin_sucesso", None)
if mensagem:
    st.success(mensagem)
aviso = st.session_state.pop("fin_aviso", None)
if aviso:
    st.warning(aviso)

# ---------------- filtros (valem para Conciliação e Resumo) ----------------
opcoes_conta = {"Todas as contas": None}
opcoes_conta.update({c["nome"]: c["id"] for c in bancos})
col_conta, col_periodo = st.columns([1, 2])
nome_conta = col_conta.selectbox("Conta bancária", list(opcoes_conta), key="fin_conta")
conta_id = opcoes_conta[nome_conta]

ultima = ultima_data_extrato(conta_id) or hoje
primeiro_dia = ultima.replace(day=1)
ultimo_dia = (primeiro_dia + timedelta(days=32)).replace(day=1) - timedelta(days=1)
periodo = col_periodo.date_input(
    "Período",
    value=(primeiro_dia, ultimo_dia),
    format="DD/MM/YYYY",
    key=f"fin_periodo_{conta_id}_{ultima}",
    help="Começa no mês do último lançamento importado. Vale para as abas Conciliação e Resumo.",
)
periodo_ok = isinstance(periodo, (tuple, list)) and len(periodo) == 2
inicio, fim = periodo if periodo_ok else (None, None)

aba_conc, aba_import, aba_resumo = st.tabs(
    ["Conciliação bancária", "Importar extrato (OFX)", "Resumo por plano de contas"]
)


# ================================================================
# Conciliação
# ================================================================
def _marcar_sugeridas():
    st.session_state["fin_marcar_sug"] = True
    st.session_state["fin_versao"] += 1


def _sinal_incompativel(valor, grupo):
    """Crédito em conta de despesa/custo ou débito em conta de receita
    (pode ser estorno, mas vale conferir)."""
    return (valor > 0 and grupo in ("despesa", "custo")) or (valor < 0 and grupo == "receita")


def _editor_pendentes(linhas):
    versao = st.session_state["fin_versao"]
    por_rotulo = {rotulo_conta(c): c for c in plano}
    rotulo_por_id = {c["id"]: rotulo_conta(c) for c in plano}
    sugestoes = sugestoes_por_descricao()
    marcar_sugeridas = st.session_state.get("fin_marcar_sug", False)

    registros = []
    for l in linhas:
        sugerida = rotulo_por_id.get(sugestoes.get(normalizar_descricao(l["descricao"])))
        registros.append(
            {
                "id": l["id"],
                "conciliar": bool(marcar_sugeridas and sugerida),
                "data": l["data"],
                "conta": l["conta"],
                "descricao": l["descricao"] or "",
                "valor": float(l["valor"]),
                "plano": sugerida,
            }
        )
    tem_sugestao = any(r["plano"] for r in registros)

    st.caption(
        "Escolha a conta do plano de contas de cada lançamento e marque **Conciliar?**. "
        "Lançamentos com descrição igual a uma já conciliada vêm com a conta sugerida — confira antes de marcar."
    )
    if tem_sugestao:
        st.button("Marcar todos que já têm conta sugerida", on_click=_marcar_sugeridas, key="fin_btn_sug")

    editado = st.data_editor(
        pd.DataFrame(registros),
        hide_index=True,
        use_container_width=True,
        disabled=["data", "conta", "descricao", "valor"],
        key=f"fin_ed_pend_{versao}_{conta_id}_{inicio}_{fim}",
        column_order=["conciliar", "data", "conta", "descricao", "valor", "plano"],
        column_config={
            "id": None,
            "conciliar": st.column_config.CheckboxColumn("Conciliar?"),
            "data": st.column_config.DateColumn("Data", format="DD/MM/YYYY"),
            "conta": st.column_config.TextColumn("Conta bancária"),
            "descricao": st.column_config.TextColumn("Descrição", width="large"),
            "valor": st.column_config.NumberColumn("Valor (R$)", format="%.2f"),
            "plano": st.column_config.SelectboxColumn(
                "Conta do plano de contas", options=list(por_rotulo), width="large", required=False
            ),
        },
    )

    marcados = editado[editado["conciliar"] == True]  # noqa: E712
    sem_conta = marcados[marcados["plano"].isna()]
    prontos = marcados[marcados["plano"].notna()]
    incompativeis = [
        r for _, r in prontos.iterrows() if _sinal_incompativel(r["valor"], por_rotulo[r["plano"]]["tipo"])
    ]

    if len(sem_conta):
        st.error(f"{len(sem_conta)} lançamento(s) marcado(s) sem conta do plano de contas — escolha a conta ou desmarque.")
    if incompativeis:
        st.warning(
            f"{len(incompativeis)} lançamento(s) com sinal diferente do grupo da conta "
            "(crédito em despesa/custo ou débito em receita). Pode ser um estorno — confira."
        )

    if st.button(
        f"Conciliar {len(prontos)} lançamento(s)" if len(prontos) else "Conciliar",
        type="primary",
        disabled=prontos.empty or bool(len(sem_conta)),
        key="fin_btn_conciliar",
    ):
        pares = [(int(r["id"]), por_rotulo[r["plano"]]["id"]) for _, r in prontos.iterrows()]
        feitos = conciliar(pares, usuario)
        st.session_state["fin_sucesso"] = f"{feitos} lançamento(s) conciliado(s)."
        if feitos < len(pares):
            st.session_state["fin_aviso"] = (
                f"{len(pares) - feitos} lançamento(s) já tinham sido conciliados por outra pessoa e foram pulados."
            )
        st.session_state["fin_marcar_sug"] = False
        st.session_state["fin_versao"] += 1
        st.rerun()


def _editor_conciliados(linhas):
    versao = st.session_state["fin_versao"]
    rotulo_por_id = {c["id"]: rotulo_conta(c) for c in plano}
    st.caption("Para corrigir uma conciliação, marque **Desfazer?** e confirme; o lançamento volta para os pendentes.")
    editado = st.data_editor(
        pd.DataFrame(
            [
                {
                    "id": l["id"],
                    "desfazer": False,
                    "data": l["data"],
                    "conta": l["conta"],
                    "descricao": l["descricao"] or "",
                    "valor": float(l["valor"]),
                    "plano": rotulo_por_id.get(l["plano_conta_id"], "(conta removida)"),
                    "por": l["conciliado_por"] or "",
                }
                for l in linhas
            ]
        ),
        hide_index=True,
        use_container_width=True,
        disabled=["data", "conta", "descricao", "valor", "plano", "por"],
        key=f"fin_ed_conc_{versao}_{conta_id}_{inicio}_{fim}",
        column_order=["desfazer", "data", "conta", "descricao", "valor", "plano", "por"],
        column_config={
            "id": None,
            "desfazer": st.column_config.CheckboxColumn("Desfazer?"),
            "data": st.column_config.DateColumn("Data", format="DD/MM/YYYY"),
            "conta": st.column_config.TextColumn("Conta bancária"),
            "descricao": st.column_config.TextColumn("Descrição", width="large"),
            "valor": st.column_config.NumberColumn("Valor (R$)", format="%.2f"),
            "plano": st.column_config.TextColumn("Conta do plano de contas", width="large"),
            "por": st.column_config.TextColumn("Conciliado por"),
        },
    )
    marcados = editado[editado["desfazer"] == True]  # noqa: E712
    if st.button(
        f"Desfazer conciliação de {len(marcados)} lançamento(s)" if len(marcados) else "Desfazer conciliação",
        disabled=marcados.empty,
        key="fin_btn_desfazer",
    ):
        feitos = desfazer_conciliacao([int(i) for i in marcados["id"]])
        st.session_state["fin_sucesso"] = f"{feitos} conciliação(ões) desfeita(s)."
        st.session_state["fin_versao"] += 1
        st.rerun()


def _tabela_todos(linhas):
    rotulo_por_id = {c["id"]: rotulo_conta(c) for c in plano}
    st.dataframe(
        [
            {
                "Data": fmt_data(l["data"]),
                "Conta bancária": l["conta"],
                "Descrição": l["descricao"] or "",
                "Valor": brl(l["valor"]),
                "Situação": "Conciliado" if l["plano_conta_id"] else "Pendente",
                "Conta do plano de contas": rotulo_por_id.get(l["plano_conta_id"], "—"),
            }
            for l in linhas
        ],
        use_container_width=True,
        hide_index=True,
    )
    st.caption("Para conciliar ou desfazer, escolha Pendentes ou Conciliados acima.")


with aba_conc:
    if not periodo_ok:
        st.info("Selecione a data inicial e a final do período.")
    else:
        resumo = resumo_periodo(conta_id, inicio, fim)
        c1, c2, c3, c4 = st.columns(4)
        with c1.container(border=True):
            st.metric("Créditos (entradas)", brl(resumo["creditos"]))
        with c2.container(border=True):
            st.metric("Débitos (saídas)", brl(resumo["debitos"]))
        with c3.container(border=True):
            st.metric("Saldo do período", brl(resumo["creditos"] + resumo["debitos"]))
        with c4.container(border=True):
            st.metric(
                "Pendentes de conciliação",
                f"{resumo['pendentes_qtd']} de {resumo['total']}",
                help="Lançamentos do extrato ainda sem conta do plano de contas.",
            )

        if resumo["total"] == 0:
            st.info(
                "Nenhum lançamento nesse período/conta. Importe o extrato na aba "
                "**Importar extrato (OFX)** ou ajuste o período."
            )
        elif not plano:
            st.warning("Cadastre o plano de contas em Cadastros → Plano de contas para poder conciliar.")
        else:
            situacao_rotulo = st.radio(
                "Mostrar", ["Pendentes", "Conciliados", "Todos"], horizontal=True, key="fin_situacao"
            )
            situacao = {"Pendentes": "pendentes", "Conciliados": "conciliados", "Todos": "todos"}[situacao_rotulo]
            linhas = carregar_extrato(conta_id, inicio, fim, situacao, limite=LIMITE_LINHAS)
            if len(linhas) > LIMITE_LINHAS:
                st.warning(f"Mostrando os primeiros {LIMITE_LINHAS} lançamentos — refine a conta ou o período.")
                linhas = linhas[:LIMITE_LINHAS]

            if not linhas:
                if situacao == "pendentes":
                    st.success("Nenhum lançamento pendente nesse período. Tudo conciliado!")
                else:
                    st.info("Nenhum lançamento nesse filtro.")
            elif situacao == "pendentes":
                _editor_pendentes(linhas)
            elif situacao == "conciliados":
                _editor_conciliados(linhas)
            else:
                _tabela_todos(linhas)


# ================================================================
# Importar extrato (OFX)
# ================================================================
with aba_import:
    if not bancos:
        st.warning(
            "Nenhuma conta bancária cadastrada. Cadastre em Cadastros → Contas e caixa "
            "(tipo Conta bancária) antes de importar."
        )
    else:
        st.caption(
            "Baixe o extrato em formato OFX no internet banking e envie aqui. Reimportar o mesmo "
            "extrato (ou um que se sobreponha a outro) não duplica lançamentos."
        )
        versao_upload = st.session_state.setdefault("fin_upload_versao", 0)
        arquivos = st.file_uploader(
            "Arquivo(s) OFX", type=["ofx", "qfx"], accept_multiple_files=True,
            key=f"fin_upload_{versao_upload}",
        )

        para_importar = []  # (nome do arquivo, conta, ofx, vincular)
        nomes_bancos = [c["nome"] for c in bancos]
        for posicao, arquivo in enumerate(arquivos or []):
            with st.container(border=True):
                st.markdown(f"**{arquivo.name}**")
                try:
                    ofx = ler_ofx(arquivo.getvalue())
                except OfxInvalido as e:
                    st.error(f"Não consegui ler esse arquivo: {e}")
                    continue

                detectada = next(
                    (
                        c for c in bancos
                        if c["ofx_conta_id"]
                        and mesma_conta(c["ofx_conta_id"], ofx["conta_id"])
                        and mesma_conta(c["ofx_banco_id"], ofx["banco_id"])
                    ),
                    None,
                )
                escolha = st.selectbox(
                    "Conta bancária deste extrato",
                    options=nomes_bancos,
                    index=nomes_bancos.index(detectada["nome"]) if detectada else None,
                    placeholder="Escolha a conta",
                    key=f"fin_conta_arq_{versao_upload}_{posicao}",
                    help="Se a conta já foi vinculada a um número de conta do banco, ela é escolhida sozinha.",
                )
                if escolha is None:
                    st.info("Escolha a conta bancária deste extrato para continuar.")
                    continue
                conta = next(c for c in bancos if c["nome"] == escolha)

                vincular = False
                liberado = True
                if not conta["ofx_conta_id"]:
                    st.info(
                        f"Primeira importação em **{conta['nome']}**: ela ficará vinculada à conta "
                        f"{ofx['banco_id'] or '?'} / {ofx['conta_id'] or '?'} do banco. Nas próximas vezes, "
                        "o sistema avisa se o arquivo for de outra conta."
                    )
                    vincular = True
                elif not (
                    mesma_conta(conta["ofx_conta_id"], ofx["conta_id"])
                    and mesma_conta(conta["ofx_banco_id"], ofx["banco_id"])
                ):
                    st.error(
                        f"Este arquivo é da conta {ofx['banco_id']} / {ofx['conta_id']}, mas "
                        f"**{conta['nome']}** está vinculada à conta {conta['ofx_banco_id']} / "
                        f"{conta['ofx_conta_id']}. Confira se escolheu a conta certa."
                    )
                    liberado = st.checkbox(
                        "Importar mesmo assim e atualizar o vínculo desta conta",
                        key=f"fin_forcar_{versao_upload}_{posicao}",
                    )
                    vincular = liberado

                ja = fitids_ja_importados(conta["id"], [l["fitid"] for l in ofx["lancamentos"]])
                creditos = sum((l["valor"] for l in ofx["lancamentos"] if l["valor"] > 0), Decimal("0"))
                debitos = sum((l["valor"] for l in ofx["lancamentos"] if l["valor"] < 0), Decimal("0"))
                m1, m2, m3, m4 = st.columns(4)
                m1.metric("Período", f"{fmt_data(ofx['data_inicio'])} a {fmt_data(ofx['data_fim'])}")
                m2.metric("Lançamentos", f"{len(ofx['lancamentos'])} ({len(ofx['lancamentos']) - len(ja)} novos)")
                m3.metric("Créditos / Débitos", f"{brl(creditos)} / {brl(debitos)}")
                m4.metric(
                    "Saldo informado pelo banco",
                    brl(ofx["saldo"]) if ofx["saldo"] is not None else "—",
                    help=f"Em {fmt_data(ofx['saldo_data'])}" if ofx["saldo_data"] else None,
                )
                if len(ja) == len(ofx["lancamentos"]):
                    st.info("Todos os lançamentos deste arquivo já foram importados; nada será adicionado.")

                if liberado:
                    para_importar.append((arquivo.name, conta, ofx, vincular))

        if para_importar and st.button(
            f"Importar {len(para_importar)} arquivo(s)", type="primary", key="fin_btn_importar"
        ):
            resultados = []
            for nome_arquivo, conta, ofx, vincular in para_importar:
                novos, ignorados = importar_lancamentos(conta["id"], nome_arquivo, ofx, usuario, vincular)
                resultados.append(f"{nome_arquivo} ({conta['nome']}): {novos} novo(s), {ignorados} já existia(m)")
            st.session_state["fin_sucesso"] = "Importação concluída — " + "; ".join(resultados) + "."
            st.session_state["fin_upload_versao"] = versao_upload + 1
            st.session_state["fin_versao"] += 1
            st.rerun()

        st.divider()
        st.subheader("Situação por conta")
        cobertura = cobertura_por_conta()
        st.dataframe(
            [
                {
                    "Conta bancária": c["conta"],
                    "Primeiro lançamento": fmt_data(c["primeiro_lancamento"]),
                    "Último lançamento": fmt_data(c["ultimo_lancamento"]),
                    "Lançamentos": c["lancamentos"],
                    "Pendentes de conciliação": c["pendentes"],
                }
                for c in cobertura
            ],
            use_container_width=True,
            hide_index=True,
        )


# ================================================================
# Resumo por plano de contas
# ================================================================
with aba_resumo:
    if not periodo_ok:
        st.info("Selecione a data inicial e a final do período.")
    else:
        linhas = resumo_por_plano(conta_id, inicio, fim)
        pend = resumo_periodo(conta_id, inicio, fim)
        st.caption(
            f"Lançamentos **conciliados** de {fmt_data(inicio)} a {fmt_data(fim)}, agrupados pelo plano de contas."
        )
        if not linhas:
            st.info("Nenhum lançamento conciliado nesse período/conta.")
        else:
            ordem = list(GRUPOS)
            linhas.sort(
                key=lambda l: (
                    ordem.index(l["tipo"]) if l["tipo"] in ordem else len(ordem),
                    l["subgrupo"].casefold(),
                    l["nome"].casefold(),
                )
            )

            st.subheader("Por grupo")
            totais_grupo = {}
            for l in linhas:
                t = totais_grupo.setdefault(l["tipo"], [Decimal("0")] * 3)
                t[0] += l["creditos"]
                t[1] += l["debitos"]
                t[2] += l["liquido"]
            st.dataframe(
                [
                    {"Grupo": GRUPOS.get(g, g), "Créditos": brl(t[0]), "Débitos": brl(t[1]), "Líquido": brl(t[2])}
                    for g, t in sorted(totais_grupo.items(), key=lambda x: ordem.index(x[0]) if x[0] in ordem else 99)
                ],
                use_container_width=True,
                hide_index=True,
            )

            st.subheader("Por conta (Grupo › Subgrupo › Analítico)")
            st.dataframe(
                [
                    {
                        "Grupo": GRUPOS.get(l["tipo"], l["tipo"]),
                        "Subgrupo": l["subgrupo"],
                        "Analítico": l["nome"],
                        "Lançamentos": l["lancamentos"],
                        "Créditos": brl(l["creditos"]),
                        "Débitos": brl(l["debitos"]),
                        "Líquido": brl(l["liquido"]),
                    }
                    for l in linhas
                ],
                use_container_width=True,
                hide_index=True,
            )

        if pend["pendentes_qtd"]:
            st.warning(
                f"Ainda há {pend['pendentes_qtd']} lançamento(s) pendente(s) neste período "
                f"(saldo {brl(pend['pendentes_valor'])}), que não entram neste resumo."
            )
