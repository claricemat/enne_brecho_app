from datetime import date, timedelta
from decimal import Decimal
from io import BytesIO

import pandas as pd
import streamlit as st

from auth import exigir_login, botao_logout
from branding import aplicar_logo
from financeiro import (
    FORMAS_COM_CONTA,
    ORIGENS,
    ConciliacaoInvalidaError,
    LancamentoInvalidoError,
    aplicar_contas_padrao,
    balancete,
    carregar_extrato,
    cobertura_por_conta,
    conciliar_com_diferenca,
    conciliar_lote,
    config_planos,
    contas_bancarias,
    contas_financeiras,
    contas_padrao,
    definir_contas,
    desfazer_conciliacao,
    excluir_manual,
    fitids_ja_importados,
    importar_lancamentos,
    lancamentos_sem_conta,
    ligar_ao_extrato,
    pares_no_extrato,
    limpar_vinculos_orfaos,
    movimentos_da_conta,
    movimentos_livres,
    movimentos_sem_extrato,
    normalizar_descricao,
    pendencias_balancete,
    registrar_manual,
    registrar_transferencia,
    resumo_periodo,
    rotulo_conta_financeira,
    salvar_config_planos,
    salvar_contas_padrao,
    salvar_saldos_iniciais,
    saldos_contas,
    sugerir_vinculos,
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
    "Extrato do banco (OFX) conciliado com o que foi registrado no sistema — vendas, recebimentos do "
    "fiado, despesas e pagamentos de compras —, lançamentos do caixa e balancete."
)

LIMITE_LINHAS = 500
hoje = hoje_brasil()
usuario = st.session_state.get("usuario")
st.session_state.setdefault("fin_versao", 0)

# movimentos que deixaram de existir (pagamento excluído, por ex.) liberam o extrato
limpar_vinculos_orfaos()

bancos = contas_bancarias()
todas_contas = contas_financeiras()
plano = carregar_contas()
por_rotulo_plano = {rotulo_conta(c): c for c in plano}
rotulo_por_id_plano = {c["id"]: rotulo_conta(c) for c in plano}
nome_conta_por_id = {c["id"]: c["nome"] for c in todas_contas}

mensagem = st.session_state.pop("fin_sucesso", None)
if mensagem:
    st.success(mensagem)
aviso = st.session_state.pop("fin_aviso", None)
if aviso:
    st.warning(aviso)

# ---------------- período (vale para Conciliação, Caixa e Balancete) ----------------
ultima = ultima_data_extrato() or hoje
primeiro_dia = ultima.replace(day=1)
ultimo_dia = (primeiro_dia + timedelta(days=32)).replace(day=1) - timedelta(days=1)
periodo = st.date_input(
    "Período",
    value=(primeiro_dia, ultimo_dia),
    format="DD/MM/YYYY",
    key=f"fin_periodo_{ultima}",
    help="Começa no mês do último lançamento importado. Vale para Conciliação, Caixa e Balancete.",
)
periodo_ok = isinstance(periodo, (tuple, list)) and len(periodo) == 2
inicio, fim = periodo if periodo_ok else (None, None)

aba_conc, aba_import, aba_caixa, aba_balancete, aba_config = st.tabs(
    ["Conciliação bancária", "Importar extrato (OFX)", "Caixa e lançamentos", "Balancete", "Configurações"]
)


def _rerun(mensagem=None, aviso=None):
    if mensagem:
        st.session_state["fin_sucesso"] = mensagem
    if aviso:
        st.session_state["fin_aviso"] = aviso
    st.session_state["fin_versao"] += 1
    st.rerun()


def _sinal_incompativel(valor, grupo):
    """Crédito em conta de despesa/custo ou débito em conta de receita
    (pode ser estorno, mas vale conferir)."""
    return (valor > 0 and grupo in ("despesa", "custo")) or (valor < 0 and grupo == "receita")


def _texto_movimento(m):
    return f"{m['descricao']} ({fmt_data(m['data'])})"


# ================================================================
# Conciliação
# ================================================================
def _marcar_por_descricao():
    st.session_state["fin_marcar_desc"] = True
    st.session_state["fin_versao"] += 1


def _editor_pendentes(linhas, conta_id):
    versao = st.session_state["fin_versao"]
    encontrados, possiveis = sugerir_vinculos(linhas, com_possiveis=True)
    sistema = {**possiveis, **encontrados}
    por_descricao = sugestoes_por_descricao()
    marcar_desc = st.session_state.get("fin_marcar_desc", False)

    registros = []
    for l in linhas:
        mov = encontrados.get(l["id"])
        possivel = possiveis.get(l["id"])
        plano_sug = None
        if mov:
            texto = _texto_movimento(mov)
        elif possivel:
            diferencas = []
            if possivel["conta_financeira_id"] != l["conta_financeira_id"]:
                diferencas.append(f"registrado em {nome_conta_por_id.get(possivel['conta_financeira_id'], '?')}")
            dias = (l["data"] - possivel["data"]).days
            if dias:
                diferencas.append(f"{abs(dias)} dia(s) {'antes' if dias > 0 else 'depois'} do extrato")
            texto = f"⚠ Possível: {_texto_movimento(possivel)} — {', '.join(diferencas)}"
        else:
            texto = ""
            plano_sug = rotulo_por_id_plano.get(por_descricao.get(normalizar_descricao(l["descricao"])))
        registros.append(
            {
                "id": l["id"],
                "conciliar": bool(mov) or bool(marcar_desc and plano_sug),
                "data": l["data"],
                "conta": l["conta"],
                "descricao": l["descricao"] or "",
                "valor": float(l["valor"]),
                "sistema": texto,
                "plano": plano_sug,
            }
        )
    n_sistema = len(encontrados)
    n_possiveis = len(possiveis)
    n_desc = sum(1 for r in registros if r["plano"])
    pre = n_sistema + n_desc
    st.info(
        f"**{pre} de {len(registros)} lançamento(s) pré-conciliado(s) ({pre / len(registros):.0%})** — "
        f"{n_sistema} encontrado(s) no sistema (já vêm marcados) e {n_desc} com conta sugerida pela "
        "descrição de conciliações anteriores."
        + (f" Mais **{n_possiveis} possível(is)** (⚠) para conferir." if n_possiveis else "")
    )
    st.caption(
        "**Encontrado no sistema**: venda, recebimento do fiado, despesa ou pagamento de compra com o mesmo "
        "valor, na mesma conta e com data próxima — a conta do plano vem de lá. **⚠ Possível**: mesmo valor, "
        "mas com a data mais longe ou registrado em outra conta; se for ele mesmo, marque Conciliar (a conta do "
        "lançamento do sistema é corrigida para a do extrato). Só escolha a **conta do plano** para o que não "
        "foi registrado em nenhuma tela (tarifa, rendimento...) — escolher a conta do plano numa linha que existe "
        "no sistema faz ela contar duas vezes no balancete."
    )
    if n_desc and not marcar_desc:
        st.button("Marcar também os sugeridos pela descrição", on_click=_marcar_por_descricao, key="fin_btn_desc")

    editado = st.data_editor(
        pd.DataFrame(registros),
        hide_index=True,
        use_container_width=True,
        disabled=["data", "conta", "descricao", "valor", "sistema"],
        key=f"fin_ed_pend_{versao}_{conta_id}_{inicio}_{fim}",
        column_order=["conciliar", "data", "conta", "descricao", "valor", "sistema", "plano"],
        column_config={
            "id": None,
            "conciliar": st.column_config.CheckboxColumn("Conciliar?"),
            "data": st.column_config.DateColumn("Data", format="DD/MM/YYYY"),
            "conta": st.column_config.TextColumn("Conta bancária"),
            "descricao": st.column_config.TextColumn("Descrição no extrato", width="medium"),
            "valor": st.column_config.NumberColumn("Valor (R$)", format="%.2f"),
            "sistema": st.column_config.TextColumn("Encontrado no sistema", width="large"),
            "plano": st.column_config.SelectboxColumn(
                "Conta do plano de contas", options=list(por_rotulo_plano), width="large", required=False
            ),
        },
    )

    marcados = editado[editado["conciliar"] == True]  # noqa: E712
    itens, sem_conta, incompativeis = [], 0, 0
    for _, r in marcados.iterrows():
        extrato_id = int(r["id"])
        if isinstance(r["plano"], str) and r["plano"]:
            conta_plano = por_rotulo_plano[r["plano"]]
            itens.append({"extrato_id": extrato_id, "plano_conta_id": conta_plano["id"]})
            incompativeis += _sinal_incompativel(r["valor"], conta_plano["tipo"])
        elif extrato_id in sistema:
            m = sistema[extrato_id]  # encontrado ou possível (marcado por quem conferiu)
            itens.append({"extrato_id": extrato_id, "movimentos": [(m["origem"], m["origem_id"], m["valor"])]})
        else:
            sem_conta += 1

    if sem_conta:
        st.error(f"{sem_conta} lançamento(s) marcado(s) sem conta do plano de contas — escolha a conta ou desmarque.")
    if incompativeis:
        st.warning(
            f"{incompativeis} lançamento(s) com sinal diferente do grupo da conta "
            "(crédito em despesa/custo ou débito em receita). Pode ser um estorno — confira."
        )
    if st.button(
        f"Conciliar {len(itens)} lançamento(s)" if itens else "Conciliar",
        type="primary", disabled=not itens or bool(sem_conta), key="fin_btn_conciliar",
    ):
        feitos, pulados = conciliar_lote(itens, usuario)
        st.session_state["fin_marcar_desc"] = False
        _rerun(
            f"{feitos} lançamento(s) conciliado(s).",
            f"{pulados} lançamento(s) foram pulados: já tinham sido conciliados (talvez pela outra pessoa)." if pulados else None,
        )

    _conciliacao_com_diferenca(linhas, sistema)


def _conciliacao_com_diferenca(linhas, sistema):
    """Um lançamento do extrato = vários do sistema (ex.: repasse da maquininha com a taxa descontada)."""
    with st.expander("Conciliar um lançamento com vários do sistema (ex.: repasse do cartão com taxa)"):
        st.caption(
            "Escolha o lançamento do extrato e os lançamentos do sistema que ele junta. Se a soma não bater "
            "(taxa da maquininha, por exemplo), a diferença é lançada na conta do plano que você escolher."
        )
        versao = st.session_state["fin_versao"]
        opcoes = {f"{fmt_data(l['data'])} — {l['conta']} — {l['descricao'] or ''} — {brl(l['valor'])}": l for l in linhas}
        escolha = st.selectbox("Lançamento do extrato", list(opcoes), index=None, placeholder="Escolha",
                               key=f"fin_multi_ext_{versao}")
        if not escolha:
            return
        e = opcoes[escolha]
        sugerido = sistema.get(e["id"])
        candidatos = [
            m for m in movimentos_livres([e["conta_financeira_id"]], e["data"] - timedelta(days=40),
                                         e["data"] + timedelta(days=7))
            if (m["valor"] > 0) == (e["valor"] > 0)
        ]
        if sugerido:  # o que foi encontrado automaticamente aparece primeiro
            candidatos.sort(key=lambda m: (m["origem"], m["origem_id"]) != (sugerido["origem"], sugerido["origem_id"]))
        if not candidatos:
            st.info("Nenhum lançamento do sistema livre nessa conta entre 40 dias antes e 7 dias depois.")
            return
        por_texto = {f"{_texto_movimento(m)} — {brl(m['valor'])}": m for m in candidatos}
        escolhidos = st.multiselect("Lançamentos do sistema", list(por_texto), key=f"fin_multi_mov_{versao}_{e['id']}")
        movs = [por_texto[t] for t in escolhidos]
        soma = sum((Decimal(m["valor"]) for m in movs), Decimal("0"))
        diferenca = Decimal(e["valor"]) - soma
        c1, c2, c3 = st.columns(3)
        c1.metric("Extrato", brl(e["valor"]))
        c2.metric("Soma do sistema", brl(soma))
        c3.metric("Diferença", brl(diferenca))
        plano_ajuste = None
        if movs and diferenca != 0:
            sugestao = next((r for r, c in por_rotulo_plano.items() if "taxa" in c["nome"].casefold()), None)
            rotulos = list(por_rotulo_plano)
            escolha_plano = st.selectbox(
                "Lançar a diferença em", rotulos, index=rotulos.index(sugestao) if sugestao else None,
                placeholder="Conta do plano (ex.: taxas de cartão)", key=f"fin_multi_plano_{versao}_{e['id']}",
            )
            plano_ajuste = por_rotulo_plano[escolha_plano]["id"] if escolha_plano else None
        if st.button("Conciliar com os lançamentos escolhidos", type="primary",
                     disabled=not movs or (diferenca != 0 and not plano_ajuste), key=f"fin_multi_btn_{versao}"):
            try:
                dif = conciliar_com_diferenca(
                    e["id"], [(m["origem"], m["origem_id"], m["valor"]) for m in movs], plano_ajuste, usuario)
            except ConciliacaoInvalidaError as erro:
                st.error(str(erro))
            else:
                _rerun(f"Lançamento conciliado com {len(movs)} lançamento(s) do sistema"
                       + (f"; diferença de {brl(dif)} lançada." if dif else "."))


def _editor_conciliados(linhas, conta_id):
    versao = st.session_state["fin_versao"]
    st.caption("Para corrigir, marque **Desfazer?** e confirme; o lançamento volta para os pendentes.")
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
                    "com": l["vinculos"] or ("Plano: " + rotulo_por_id_plano.get(l["plano_conta_id"], "(conta removida)")
                                             if l["plano_conta_id"] else "—"),
                    "por": l["conciliado_por"] or "",
                }
                for l in linhas
            ]
        ),
        hide_index=True,
        use_container_width=True,
        disabled=["data", "conta", "descricao", "valor", "com", "por"],
        key=f"fin_ed_conc_{versao}_{conta_id}_{inicio}_{fim}",
        column_order=["desfazer", "data", "conta", "descricao", "valor", "com", "por"],
        column_config={
            "id": None,
            "desfazer": st.column_config.CheckboxColumn("Desfazer?"),
            "data": st.column_config.DateColumn("Data", format="DD/MM/YYYY"),
            "conta": st.column_config.TextColumn("Conta bancária"),
            "descricao": st.column_config.TextColumn("Descrição no extrato", width="medium"),
            "valor": st.column_config.NumberColumn("Valor (R$)", format="%.2f"),
            "com": st.column_config.TextColumn("Conciliado com", width="large"),
            "por": st.column_config.TextColumn("Por"),
        },
    )
    marcados = editado[editado["desfazer"] == True]  # noqa: E712
    col1, col2 = st.columns(2)
    if col1.button(
        f"Desfazer conciliação de {len(marcados)} lançamento(s)" if len(marcados) else "Desfazer conciliação",
        disabled=marcados.empty, key="fin_btn_desfazer",
    ):
        feitos = desfazer_conciliacao([int(i) for i in marcados["id"]])
        _rerun(f"{feitos} conciliação(ões) desfeita(s).")
    with col2.popover(f"Desfazer todas as {len(linhas)} desta lista"):
        st.caption("Útil para refazer a conciliação usando o que foi registrado no sistema.")
        ok = st.checkbox("Confirmo: desfazer todas", key=f"fin_desfazer_todas_ok_{versao}")
        if st.button("Desfazer todas", disabled=not ok, key=f"fin_desfazer_todas_{versao}"):
            feitos = desfazer_conciliacao([l["id"] for l in linhas])
            _rerun(f"{feitos} conciliação(ões) desfeita(s).")


def _tabela_todos(linhas):
    st.dataframe(
        [
            {
                "Data": fmt_data(l["data"]),
                "Conta bancária": l["conta"],
                "Descrição": l["descricao"] or "",
                "Valor": brl(l["valor"]),
                "Situação": "Conciliado" if l["conciliado_em"] else "Pendente",
                "Conciliado com": l["vinculos"] or rotulo_por_id_plano.get(l["plano_conta_id"], "—"),
            }
            for l in linhas
        ],
        use_container_width=True,
        hide_index=True,
    )
    st.caption("Para conciliar ou desfazer, escolha Pendentes ou Conciliados acima.")


def _secao_sem_extrato(sem_extrato):
    """Movimentos do sistema soltos: mostra o lançamento do extrato que parece ser o
    mesmo (inclusive os já conciliados direto no plano) e permite ligar."""
    versao = st.session_state["fin_versao"]
    pares = pares_no_extrato(sem_extrato)
    com_par = sum(1 for m in sem_extrato if (m["origem"], m["origem_id"]) in pares)
    duplicados = sum(1 for l in pares.values() if l["plano_conta_id"])
    titulo = f"No sistema, mas ainda não ligados ao extrato: {len(sem_extrato)} lançamento(s)"
    if com_par:
        titulo += f" — {com_par} com par encontrado no extrato"
    with st.expander(titulo, expanded=bool(duplicados)):
        if duplicados:
            st.warning(
                f"{duplicados} desses lançamentos já estão no extrato **conciliados direto numa conta do plano** — "
                "assim eles contam duas vezes no balancete. Ligue-os abaixo: a classificação direta é trocada "
                "pela ligação com o lançamento do sistema."
            )
        st.caption(
            "Lançamentos registrados nas contas bancárias que ainda não estão ligados ao extrato. Quando há um "
            "lançamento de mesmo valor no extrato (até 20 dias de diferença), ele aparece em **No extrato**; os da "
            "mesma conta já vêm marcados. Ao ligar um que foi registrado em outra conta, a conta do lançamento do "
            "sistema é corrigida para a do extrato. Sem par: o extrato ainda não chegou nessa data, a conta não teve "
            "extrato importado ou o valor não bateu."
        )
        registros = []
        for m in sem_extrato:
            l = pares.get((m["origem"], m["origem_id"]))
            texto = ""
            if l:
                if l["plano_conta_id"]:
                    situacao = "conciliado direto em " + rotulo_por_id_plano.get(l["plano_conta_id"], "?")
                else:
                    situacao = "pendente"
                texto = f"{fmt_data(l['data'])} — {l['conta']} — {l['descricao'] or ''} ({situacao})"
                if l["conta_financeira_id"] != m["conta_financeira_id"]:
                    texto = "⚠ outra conta: " + texto
            registros.append(
                {
                    "origem": m["origem"], "origem_id": m["origem_id"], "extrato_id": l["id"] if l else None,
                    "ligar": bool(l) and l["conta_financeira_id"] == m["conta_financeira_id"],
                    "data": m["data"], "conta": nome_conta_por_id.get(m["conta_financeira_id"], ""),
                    "tipo": ORIGENS.get(m["origem"], m["origem"]), "descricao": m["descricao"],
                    "valor": float(m["valor"]), "extrato": texto,
                }
            )
        editado = st.data_editor(
            pd.DataFrame(registros),
            hide_index=True,
            use_container_width=True,
            num_rows="fixed",
            disabled=["data", "conta", "tipo", "descricao", "valor", "extrato"],
            key=f"fin_sem_extrato_{versao}_{inicio}_{fim}",
            column_order=["ligar", "data", "conta", "tipo", "descricao", "valor", "extrato"],
            column_config={
                "origem": None, "origem_id": None, "extrato_id": None,
                "ligar": st.column_config.CheckboxColumn("Ligar?"),
                "data": st.column_config.DateColumn("Data", format="DD/MM/YYYY"),
                "conta": st.column_config.TextColumn("Conta registrada"),
                "tipo": st.column_config.TextColumn("Origem"),
                "descricao": st.column_config.TextColumn("Descrição", width="medium"),
                "valor": st.column_config.NumberColumn("Valor (R$)", format="%.2f"),
                "extrato": st.column_config.TextColumn("No extrato", width="large"),
            },
        )
        escolhidos = [
            (int(r["extrato_id"]), r["origem"], int(r["origem_id"]), r["valor"])
            for _, r in editado.iterrows()
            if r["ligar"] and pd.notna(r["extrato_id"])
        ]
        marcados_sem_par = sum(1 for _, r in editado.iterrows() if r["ligar"] and pd.isna(r["extrato_id"]))
        if marcados_sem_par:
            st.caption(f"{marcados_sem_par} marcado(s) sem par no extrato serão ignorados.")
        if st.button(f"Ligar {len(escolhidos)} ao extrato" if escolhidos else "Ligar ao extrato",
                     type="primary", disabled=not escolhidos, key=f"fin_ligar_{versao}"):
            ligados, pulados = ligar_ao_extrato(escolhidos, usuario)
            _rerun(f"{ligados} lançamento(s) ligado(s) ao extrato.",
                   f"{pulados} pulado(s): o lançamento do extrato já estava ligado a outro." if pulados else None)


with aba_conc:
    opcoes_conta = {"Todas as contas": None}
    opcoes_conta.update({c["nome"]: c["id"] for c in bancos})
    nome_conta = st.selectbox("Conta bancária", list(opcoes_conta), key="fin_conta")
    conta_id = opcoes_conta[nome_conta]

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
            st.metric("Pendentes de conciliação", f"{resumo['pendentes_qtd']} de {resumo['total']}",
                      help="Lançamentos do extrato ainda não conciliados.")

        if resumo["total"] == 0:
            st.info("Nenhum lançamento nesse período/conta. Importe o extrato na aba "
                    "**Importar extrato (OFX)** ou ajuste o período.")
        else:
            situacao_rotulo = st.radio("Mostrar", ["Pendentes", "Conciliados", "Todos"], horizontal=True,
                                       key="fin_situacao")
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
                _editor_pendentes(linhas, conta_id)
            elif situacao == "conciliados":
                _editor_conciliados(linhas, conta_id)
            else:
                _tabela_todos(linhas)

        sem_extrato = movimentos_sem_extrato(conta_id, inicio, fim)
        if sem_extrato:
            _secao_sem_extrato(sem_extrato)


# ================================================================
# Importar extrato (OFX)
# ================================================================
with aba_import:
    if not bancos:
        st.warning("Nenhuma conta bancária cadastrada. Cadastre em Cadastros → Contas e caixa "
                   "(tipo Conta bancária) antes de importar.")
    else:
        st.caption(
            "Baixe o extrato em formato OFX no internet banking e envie aqui. Reimportar o mesmo "
            "extrato (ou um que se sobreponha a outro) não duplica lançamentos."
        )
        versao_upload = st.session_state.setdefault("fin_upload_versao", 0)
        arquivos = st.file_uploader("Arquivo(s) OFX", type=["ofx", "qfx"], accept_multiple_files=True,
                                    key=f"fin_upload_{versao_upload}")

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
                    (c for c in bancos if c["ofx_conta_id"]
                     and mesma_conta(c["ofx_conta_id"], ofx["conta_id"])
                     and mesma_conta(c["ofx_banco_id"], ofx["banco_id"])),
                    None,
                )
                escolha = st.selectbox(
                    "Conta bancária deste extrato", options=nomes_bancos,
                    index=nomes_bancos.index(detectada["nome"]) if detectada else None,
                    placeholder="Escolha a conta", key=f"fin_conta_arq_{versao_upload}_{posicao}",
                    help="Se a conta já foi vinculada a um número de conta do banco, ela é escolhida sozinha.",
                )
                if escolha is None:
                    st.info("Escolha a conta bancária deste extrato para continuar.")
                    continue
                conta = next(c for c in bancos if c["nome"] == escolha)

                vincular, liberado = False, True
                if not conta["ofx_conta_id"]:
                    st.info(
                        f"Primeira importação em **{conta['nome']}**: ela ficará vinculada à conta "
                        f"{ofx['banco_id'] or '?'} / {ofx['conta_id'] or '?'} do banco. Nas próximas vezes, "
                        "o sistema avisa se o arquivo for de outra conta."
                    )
                    vincular = True
                elif not (mesma_conta(conta["ofx_conta_id"], ofx["conta_id"])
                          and mesma_conta(conta["ofx_banco_id"], ofx["banco_id"])):
                    st.error(
                        f"Este arquivo é da conta {ofx['banco_id']} / {ofx['conta_id']}, mas "
                        f"**{conta['nome']}** está vinculada à conta {conta['ofx_banco_id']} / "
                        f"{conta['ofx_conta_id']}. Confira se escolheu a conta certa."
                    )
                    liberado = st.checkbox("Importar mesmo assim e atualizar o vínculo desta conta",
                                           key=f"fin_forcar_{versao_upload}_{posicao}")
                    vincular = liberado

                ja = fitids_ja_importados(conta["id"], [l["fitid"] for l in ofx["lancamentos"]])
                creditos = sum((l["valor"] for l in ofx["lancamentos"] if l["valor"] > 0), Decimal("0"))
                debitos = sum((l["valor"] for l in ofx["lancamentos"] if l["valor"] < 0), Decimal("0"))
                m1, m2, m3, m4 = st.columns(4)
                m1.metric("Período", f"{fmt_data(ofx['data_inicio'])} a {fmt_data(ofx['data_fim'])}")
                m2.metric("Lançamentos", f"{len(ofx['lancamentos'])} ({len(ofx['lancamentos']) - len(ja)} novos)")
                m3.metric("Créditos / Débitos", f"{brl(creditos)} / {brl(debitos)}")
                m4.metric("Saldo informado pelo banco", brl(ofx["saldo"]) if ofx["saldo"] is not None else "—",
                          help=f"Em {fmt_data(ofx['saldo_data'])}" if ofx["saldo_data"] else None)
                if len(ja) == len(ofx["lancamentos"]):
                    st.info("Todos os lançamentos deste arquivo já foram importados; nada será adicionado.")
                if liberado:
                    para_importar.append((arquivo.name, conta, ofx, vincular))

        if para_importar and st.button(f"Importar {len(para_importar)} arquivo(s)", type="primary",
                                       key="fin_btn_importar"):
            resultados = []
            for nome_arquivo, conta, ofx, vincular in para_importar:
                novos, ignorados = importar_lancamentos(conta["id"], nome_arquivo, ofx, usuario, vincular)
                resultados.append(f"{nome_arquivo} ({conta['nome']}): {novos} novo(s), {ignorados} já existia(m)")
            st.session_state["fin_upload_versao"] = versao_upload + 1
            _rerun("Importação concluída — " + "; ".join(resultados)
                   + ". Veja a conciliação na aba Conciliação bancária: o que foi encontrado no sistema já vem marcado.")

        st.divider()
        st.subheader("Situação por conta")
        st.dataframe(
            [
                {
                    "Conta bancária": c["conta"],
                    "Primeiro lançamento": fmt_data(c["primeiro_lancamento"]),
                    "Último lançamento": fmt_data(c["ultimo_lancamento"]),
                    "Lançamentos": c["lancamentos"],
                    "Pendentes de conciliação": c["pendentes"],
                }
                for c in cobertura_por_conta()
            ],
            use_container_width=True,
            hide_index=True,
        )


# ================================================================
# Caixa e lançamentos manuais
# ================================================================
with aba_caixa:
    if not todas_contas:
        st.warning("Cadastre o caixa e as contas bancárias em Cadastros → Contas e caixa.")
    elif not periodo_ok:
        st.info("Selecione a data inicial e a final do período.")
    else:
        versao = st.session_state["fin_versao"]
        opcoes_cf = {rotulo_conta_financeira(c): c for c in todas_contas}
        rotulos_cf = list(opcoes_cf)
        indice_caixa = next((i for i, c in enumerate(todas_contas) if c["tipo"] == "caixa"), 0)
        rotulo_sel = st.selectbox("Conta", rotulos_cf, index=indice_caixa, key="fin_caixa_conta")
        conta_sel = opcoes_cf[rotulo_sel]

        saldo = next(s for s in saldos_contas(inicio, fim) if s["id"] == conta_sel["id"])
        c1, c2, c3, c4 = st.columns(4)
        with c1.container(border=True):
            st.metric(f"Saldo em {fmt_data(inicio - timedelta(days=1))}", brl(saldo["saldo_anterior"]))
        with c2.container(border=True):
            st.metric("Entradas", brl(saldo["entradas"]))
        with c3.container(border=True):
            st.metric("Saídas", brl(saldo["saidas"]))
        with c4.container(border=True):
            st.metric(f"Saldo em {fmt_data(fim)}", brl(saldo["saldo_final"]))
        if not conta_sel["saldo_inicial_data"]:
            st.caption("Saldo inicial ainda não informado (considerado R$ 0,00) — informe em Configurações.")
        else:
            st.caption(f"A partir do saldo inicial de {brl(conta_sel['saldo_inicial'])} em "
                       f"{fmt_data(conta_sel['saldo_inicial_data'])}.")

        # ---- novo lançamento ----
        with st.container(border=True):
            st.markdown("**Novo lançamento**")
            tipo = st.radio("Tipo", ["Entrada", "Saída", "Transferência entre contas"], horizontal=True,
                            key=f"fin_man_tipo_{versao}",
                            help="Transferência: ex. depósito do dinheiro do caixa no banco, ou retirada do banco "
                                 "para o troco. Sai de uma conta e entra na outra; não é receita nem despesa.")
            col1, col2 = st.columns(2)
            data_man = col1.date_input("Data", value=min(hoje, fim), max_value=hoje, format="DD/MM/YYYY",
                                       key=f"fin_man_data_{versao}")
            valor_man = col2.number_input("Valor (R$)", min_value=0.0, step=1.0, format="%.2f",
                                          key=f"fin_man_valor_{versao}")
            if tipo == "Transferência entre contas":
                col3, col4 = st.columns(2)
                de = col3.selectbox("Sai de", rotulos_cf, index=rotulos_cf.index(rotulo_sel), key=f"fin_man_de_{versao}")
                outras = [r for r in rotulos_cf if r != de]
                para = col4.selectbox("Entra em", outras, index=None, placeholder="Escolha a conta",
                                      key=f"fin_man_para_{versao}")
                desc_man = st.text_input("Descrição (opcional)", placeholder="ex: depósito do caixa",
                                         key=f"fin_man_desc_{versao}")
                pronto = valor_man > 0 and para is not None
                if st.button("Registrar transferência", type="primary", disabled=not pronto,
                             key=f"fin_man_btn_t_{versao}"):
                    try:
                        registrar_transferencia(opcoes_cf[de]["id"], opcoes_cf[para]["id"], data_man, valor_man,
                                                desc_man.strip(), usuario)
                    except LancamentoInvalidoError as e:
                        st.error(str(e))
                    else:
                        _rerun(f"Transferência de {brl(valor_man)} registrada: {de} → {para}.")
            else:
                col3, col4 = st.columns(2)
                conta_man = col3.selectbox("Conta", rotulos_cf, index=rotulos_cf.index(rotulo_sel),
                                           key=f"fin_man_conta_{versao}")
                grupos_ok = ("receita", "transferencia") if tipo == "Entrada" else ("despesa", "custo", "transferencia")
                planos_ok = [r for r, c in por_rotulo_plano.items() if c["tipo"] in grupos_ok]
                plano_man = col4.selectbox("Conta do plano de contas", planos_ok, index=None,
                                           placeholder="Ex.: Outras receitas, Transporte…", key=f"fin_man_plano_{versao}")
                desc_man = st.text_input("Descrição", placeholder="ex: troco inicial, lanche, frete pago em dinheiro",
                                         key=f"fin_man_desc_{versao}")
                if opcoes_cf[conta_man]["tipo"] == "banco":
                    st.caption("Atenção: em conta bancária, o lançamento manual também precisa aparecer no extrato "
                               "para ser conciliado. Use para o que não foi registrado em outra tela.")
                pronto = valor_man > 0 and plano_man is not None
                if st.button(f"Registrar {tipo.lower()}", type="primary", disabled=not pronto,
                             key=f"fin_man_btn_{versao}"):
                    sinal = 1 if tipo == "Entrada" else -1
                    try:
                        registrar_manual(opcoes_cf[conta_man]["id"], data_man, Decimal(str(valor_man)) * sinal,
                                         por_rotulo_plano[plano_man]["id"], desc_man.strip(), usuario)
                    except LancamentoInvalidoError as e:
                        st.error(str(e))
                    else:
                        _rerun(f"{tipo} de {brl(valor_man)} registrada em {conta_man}.")

        # ---- movimentos da conta no período ----
        st.subheader(f"Movimentos de {conta_sel['nome']} no período")
        movs = movimentos_da_conta(conta_sel["id"], inicio, fim)
        if not movs:
            st.info("Nenhum movimento nesse período.")
        else:
            saldo_corrente = Decimal(saldo["saldo_anterior"])
            linhas_tab = []
            for m in movs:
                saldo_corrente += Decimal(m["valor"])
                origem = "Extrato (classificado no plano)" if m["origem"] == "extrato" else ORIGENS.get(m["origem"], m["origem"])
                if m["transferencia"]:
                    origem = "Transferência"
                elif m["ajuste"]:
                    origem = "Diferença da conciliação"
                linhas_tab.append(
                    {
                        "Data": fmt_data(m["data"]),
                        "Origem": origem,
                        "Descrição": m["descricao"],
                        "Conta do plano": rotulo_por_id_plano.get(m["plano_conta_id"], "—"),
                        "Entrada": brl(m["valor"]) if m["valor"] > 0 else "",
                        "Saída": brl(-m["valor"]) if m["valor"] < 0 else "",
                        "Saldo": brl(saldo_corrente),
                        "Conciliado": ("Sim" if m["conciliado"] else "Não") if conta_sel["tipo"] == "banco" else "—",
                    }
                )
            st.dataframe(linhas_tab, use_container_width=True, hide_index=True)

            manuais = [m for m in movs if m["origem"] == "manual" and not m["ajuste"]]
            if manuais:
                with st.expander("Excluir um lançamento manual"):
                    st.caption("Transferência: as duas pontas são excluídas juntas. Lançamento já conciliado "
                               "com o extrato não pode ser excluído (desfaça a conciliação antes).")
                    opcoes_exc = {f"{fmt_data(m['data'])} — {m['descricao']} — {brl(m['valor'])}": m["origem_id"]
                                  for m in manuais}
                    escolha_exc = st.selectbox("Lançamento", list(opcoes_exc), key=f"fin_man_exc_sel_{versao}")
                    if st.button("Excluir lançamento", key=f"fin_man_exc_btn_{versao}"):
                        try:
                            n = excluir_manual(opcoes_exc[escolha_exc])
                        except LancamentoInvalidoError as e:
                            st.error(str(e))
                        else:
                            _rerun(f"{n} lançamento(s) excluído(s).")


# ================================================================
# Balancete
# ================================================================
def _linhas_balancete(dados):
    """Grupo > Subgrupo > Analítico com subtotais. Retorna (linhas, resultado)."""
    ordem = list(GRUPOS)
    dados = sorted(
        dados,
        key=lambda l: (ordem.index(l["tipo"]) if l["tipo"] in ordem else len(ordem),
                       (l["subgrupo"] or "").casefold(), (l["nome"] or "").casefold()),
    )
    linhas, resultado = [], Decimal("0")
    grupos = {}
    for l in dados:
        grupos.setdefault(l["tipo"], {}).setdefault(l["subgrupo"], []).append(l)

    def soma(itens):
        return [sum((Decimal(i[k]) for i in itens), Decimal("0")) for k in ("entradas", "saidas", "saldo")]

    for tipo, subgrupos in grupos.items():
        todos = [i for itens in subgrupos.values() for i in itens]
        e, s, t = soma(todos)
        nome_grupo = GRUPOS.get(tipo, "Sem classificação") if tipo else "Sem classificação"
        linhas.append({"nivel": 1, "Conta": nome_grupo.upper(), "Entradas": e, "Saídas": s, "Saldo": t})
        if tipo in ("receita", "custo", "despesa") or tipo is None:
            resultado += t
        for sub, itens in subgrupos.items():
            if tipo:
                e, s, t = soma(itens)
                linhas.append({"nivel": 2, "Conta": f"   {sub}", "Entradas": e, "Saídas": s, "Saldo": t})
            for i in itens:
                nome = i["nome"] or "Lançamentos sem conta do plano"
                linhas.append({"nivel": 3, "Conta": f"      {nome}", "Entradas": Decimal(i["entradas"]),
                               "Saídas": Decimal(i["saidas"]), "Saldo": Decimal(i["saldo"])})
    return linhas, resultado


with aba_balancete:
    if not periodo_ok:
        st.info("Selecione a data inicial e a final do período.")
    else:
        st.caption(
            f"De {fmt_data(inicio)} a {fmt_data(fim)}, pelo regime de caixa: o que entrou e saiu das contas e do "
            "caixa — registrado no sistema (vendas, recebimentos do fiado, despesas pagas, pagamentos de compras, "
            "devoluções, lançamentos manuais) mais o que foi classificado direto no plano na conciliação."
        )
        pend = pendencias_balancete(inicio, fim)
        if pend["extrato_qtd"]:
            st.warning(f"{pend['extrato_qtd']} lançamento(s) do extrato neste período ainda não foram conciliados "
                       f"({brl(pend['extrato_valor'])}) — o que não está no sistema fica fora do balancete.")
        if pend["sem_conta"]:
            st.warning(f"{pend['sem_conta']} venda(s)/despesa(s) do período estão sem conta ou caixa e ficam fora "
                       "do balancete — preencha em Configurações → Lançamentos sem conta.")

        st.subheader("Saldos das contas")
        saldos = saldos_contas(inicio, fim)
        tabela_saldos = [
            {
                "Conta": rotulo_conta_financeira(s),
                "Saldo anterior": Decimal(s["saldo_anterior"]),
                "Entradas": Decimal(s["entradas"]),
                "Saídas": Decimal(s["saidas"]),
                "Saldo final": Decimal(s["saldo_final"]),
                "Saldo pelo extrato": Decimal(s["saldo_extrato"]) if s["saldo_extrato"] is not None else None,
            }
            for s in saldos
        ]
        total_saldos = {k: sum((l[k] for l in tabela_saldos), Decimal("0"))
                        for k in ("Saldo anterior", "Entradas", "Saídas", "Saldo final")}
        st.dataframe(
            [
                {k: (brl(v) if isinstance(v, Decimal) else ("—" if v is None else v)) for k, v in l.items()}
                for l in tabela_saldos + [{"Conta": "TOTAL", **total_saldos, "Saldo pelo extrato": None}]
            ],
            use_container_width=True,
            hide_index=True,
        )
        st.caption("Saldo final = saldo inicial (Configurações) + tudo o que foi registrado até o fim do período. "
                   "Nas contas bancárias, \"Saldo pelo extrato\" soma os lançamentos importados — se for diferente, "
                   "falta conciliar ou registrar algo.")

        st.subheader("Balancete por plano de contas")
        dados = balancete(inicio, fim)
        if not dados:
            st.info("Nenhum movimento nesse período.")
        else:
            linhas_bal, resultado = _linhas_balancete(dados)
            df = pd.DataFrame(linhas_bal)
            exibir = df.drop(columns=["nivel"]).copy()
            for col in ("Entradas", "Saídas", "Saldo"):
                exibir[col] = exibir[col].map(brl)
            niveis = df["nivel"].tolist()
            st.dataframe(
                exibir.style.apply(
                    lambda linha: ["font-weight: bold; background-color: rgba(214, 79, 122, 0.12)"
                                   if niveis[linha.name] == 1 else
                                   ("font-weight: bold" if niveis[linha.name] == 2 else "")] * len(linha),
                    axis=1,
                ),
                use_container_width=True,
                hide_index=True,
                height=min(38 * (len(exibir) + 1), 900),
            )
            r1, r2, r3 = st.columns(3)
            receitas = sum((Decimal(l["saldo"]) for l in dados if l["tipo"] == "receita"), Decimal("0"))
            gastos = sum((Decimal(l["saldo"]) for l in dados if l["tipo"] in ("custo", "despesa")), Decimal("0"))
            with r1.container(border=True):
                st.metric("Receitas", brl(receitas))
            with r2.container(border=True):
                st.metric("Custos + despesas", brl(gastos))
            with r3.container(border=True):
                st.metric("Resultado do período", brl(resultado),
                          help="Receitas − custos − despesas (inclui lançamentos sem conta do plano). "
                               "Transferências entre contas não entram.")

            buffer = BytesIO()
            with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
                bal = df.drop(columns=["nivel"]).copy()
                bal["Conta"] = bal["Conta"].str.replace("   ", "    ")
                for col in ("Entradas", "Saídas", "Saldo"):
                    bal[col] = bal[col].astype(float)
                bal.loc[len(bal)] = ["RESULTADO DO PERÍODO", None, None, float(resultado)]
                bal.to_excel(writer, sheet_name="Balancete", index=False)
                sal = pd.DataFrame(tabela_saldos)
                for col in ("Saldo anterior", "Entradas", "Saídas", "Saldo final", "Saldo pelo extrato"):
                    sal[col] = sal[col].astype(float)
                sal.to_excel(writer, sheet_name="Saldos das contas", index=False)
            buffer.seek(0)
            st.download_button("Baixar balancete (Excel)", data=buffer,
                               file_name=f"balancete_enne_brecho_{inicio}_a_{fim}.xlsx",
                               mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


# ================================================================
# Configurações
# ================================================================
with aba_config:
    if not todas_contas:
        st.warning("Cadastre o caixa e as contas bancárias em Cadastros → Contas e caixa.")
    else:
        versao = st.session_state["fin_versao"]
        opcoes_cf = {rotulo_conta_financeira(c): c["id"] for c in todas_contas}
        rotulo_cf_por_id = {v: k for k, v in opcoes_cf.items()}

        # ---- conta padrão por forma ----
        st.subheader("Conta padrão de cada forma de pagamento")
        st.caption("Já vem escolhida na venda e no recebimento do fiado (dá para trocar na hora). "
                   "Ex.: Dinheiro → Caixa; Pix → conta do banco; Cartão → conta onde a maquininha deposita.")
        padroes = contas_padrao()
        novos_padroes = {}
        colunas = st.columns(len(FORMAS_COM_CONTA))
        for col, forma in zip(colunas, FORMAS_COM_CONTA):
            atual = rotulo_cf_por_id.get(padroes.get(forma))
            escolha = col.selectbox(forma, list(opcoes_cf), index=list(opcoes_cf).index(atual) if atual else None,
                                    placeholder="Sem padrão", key=f"fin_cfg_forma_{forma}_{versao}")
            novos_padroes[forma] = opcoes_cf[escolha] if escolha else None
        if st.button("Salvar contas padrão", key=f"fin_cfg_padrao_btn_{versao}",
                     disabled=novos_padroes == {f: padroes.get(f) for f in FORMAS_COM_CONTA}):
            salvar_contas_padrao(novos_padroes)
            _rerun("Contas padrão salvas.")

        # ---- saldo inicial ----
        st.divider()
        st.subheader("Saldo inicial das contas e do caixa")
        st.caption("Saldo de cada conta no início do dia escolhido (ex.: o dia em que começou a usar o sistema). "
                   "Os movimentos antes dessa data não entram no saldo.")
        df_saldos = pd.DataFrame(
            [{"id": c["id"], "conta": rotulo_conta_financeira(c), "saldo": float(c["saldo_inicial"] or 0),
              "data": c["saldo_inicial_data"]} for c in todas_contas]
        )
        editado_saldos = st.data_editor(
            df_saldos, hide_index=True, use_container_width=True, num_rows="fixed", disabled=["conta"],
            key=f"fin_cfg_saldos_{versao}",
            column_config={
                "id": None,
                "conta": st.column_config.TextColumn("Conta"),
                "saldo": st.column_config.NumberColumn("Saldo inicial (R$)", format="%.2f", step=0.01),
                "data": st.column_config.DateColumn("Em (início do dia)", format="DD/MM/YYYY"),
            },
        )
        mudancas = []
        for (_, novo), (_, velho) in zip(editado_saldos.iterrows(), df_saldos.iterrows()):
            data_nova = novo["data"].date() if isinstance(novo["data"], pd.Timestamp) else (None if pd.isna(novo["data"]) else novo["data"])
            valor_novo = 0.0 if pd.isna(novo["saldo"]) else float(novo["saldo"])
            if data_nova != velho["data"] or round(valor_novo, 2) != round(velho["saldo"], 2):
                mudancas.append((int(novo["id"]), valor_novo, data_nova))
        if st.button("Salvar saldos iniciais", disabled=not mudancas, key=f"fin_cfg_saldos_btn_{versao}"):
            salvar_saldos_iniciais(mudancas)
            _rerun("Saldos iniciais salvos.")

        # ---- contas do plano automáticas ----
        st.divider()
        st.subheader("Contas do plano usadas automaticamente")
        cfg = config_planos()
        rotulos_plano = list(por_rotulo_plano)
        cc1, cc2 = st.columns(2)
        atual_compra = rotulo_por_id_plano.get(cfg.get("pagamento_compra"))
        sel_compra = cc1.selectbox(
            "Pagamento de compras de peças (fornecedoras, bazar...)", rotulos_plano,
            index=rotulos_plano.index(atual_compra) if atual_compra else None, placeholder="Escolha",
            key=f"fin_cfg_compra_{versao}",
        )
        atual_transf = rotulo_por_id_plano.get(cfg.get("transferencia"))
        sel_transf = cc2.selectbox(
            "Transferência entre contas", rotulos_plano,
            index=rotulos_plano.index(atual_transf) if atual_transf else None, placeholder="Escolha",
            key=f"fin_cfg_transf_{versao}",
        )
        novo_cfg = {"pagamento_compra": por_rotulo_plano[sel_compra]["id"] if sel_compra else None,
                    "transferencia": por_rotulo_plano[sel_transf]["id"] if sel_transf else None}
        if st.button("Salvar", disabled=novo_cfg == {k: cfg.get(k) for k in novo_cfg}, key=f"fin_cfg_planos_btn_{versao}"):
            salvar_config_planos(novo_cfg)
            _rerun("Contas do plano salvas.")

        # ---- lançamentos sem conta ----
        st.divider()
        st.subheader("Lançamentos sem conta")
        st.caption("Vendas, recebimentos do fiado, despesas pagas e reembolsos registrados sem dizer em qual conta "
                   "ou caixa o dinheiro entrou/saiu (os de antes desta atualização). Sem a conta, eles não são "
                   "encontrados na conciliação nem entram no balancete.")
        cs1, _ = st.columns(2)
        periodo_sc = cs1.date_input("Período", value=(date(hoje.year, 1, 1), hoje), format="DD/MM/YYYY",
                                    key="fin_cfg_sc_periodo")
        if isinstance(periodo_sc, (tuple, list)) and len(periodo_sc) == 2:
            ini_sc, fim_sc = periodo_sc
            pendentes_sc = lancamentos_sem_conta(ini_sc, fim_sc)
            if not pendentes_sc:
                st.success("Nenhum lançamento sem conta nesse período.")
            else:
                padroes = contas_padrao()
                st.markdown(f"**{len(pendentes_sc)} lançamento(s) sem conta.**")
                if st.button("Preencher vendas e recebimentos do fiado com a conta padrão de cada forma",
                             disabled=not padroes, key=f"fin_cfg_aplicar_{versao}",
                             help="Usa as contas padrão escolhidas acima. Despesas e devoluções ficam para "
                                  "preencher na tabela."):
                    n = aplicar_contas_padrao(ini_sc, fim_sc)
                    _rerun(f"{n} lançamento(s) preenchido(s) com a conta padrão.")
                df_sc = pd.DataFrame(
                    [
                        {
                            "origem": p["origem"], "id": p["id"], "tipo": ORIGENS.get(p["origem"], p["origem"]),
                            "data": p["data"], "descricao": p["descricao"], "forma": p["forma"] or "",
                            "valor": float(p["valor"]),
                            "conta": rotulo_cf_por_id.get(padroes.get(p["forma"])) if p["origem"] in ("venda", "fiado") else None,
                        }
                        for p in pendentes_sc[:500]
                    ]
                )
                editado_sc = st.data_editor(
                    df_sc, hide_index=True, use_container_width=True, num_rows="fixed",
                    disabled=["tipo", "data", "descricao", "forma", "valor"],
                    key=f"fin_cfg_sc_ed_{versao}_{ini_sc}_{fim_sc}",
                    column_order=["tipo", "data", "descricao", "forma", "valor", "conta"],
                    column_config={
                        "origem": None, "id": None,
                        "tipo": st.column_config.TextColumn("Tipo"),
                        "data": st.column_config.DateColumn("Data", format="DD/MM/YYYY"),
                        "descricao": st.column_config.TextColumn("Descrição", width="large"),
                        "forma": st.column_config.TextColumn("Forma"),
                        "valor": st.column_config.NumberColumn("Valor (R$)", format="%.2f"),
                        "conta": st.column_config.SelectboxColumn("Conta ou caixa", options=list(opcoes_cf),
                                                                  width="medium"),
                    },
                )
                preenchidas = [(r["origem"], int(r["id"]), opcoes_cf[r["conta"]])
                               for _, r in editado_sc.iterrows() if isinstance(r["conta"], str) and r["conta"]]
                st.caption("Na tabela, a conta das vendas já aparece sugerida pela conta padrão — confira e salve.")
                if st.button(f"Salvar {len(preenchidas)} conta(s)" if preenchidas else "Salvar contas",
                             type="primary", disabled=not preenchidas, key=f"fin_cfg_sc_btn_{versao}"):
                    n = definir_contas(preenchidas)
                    _rerun(f"Conta informada em {n} lançamento(s).")
