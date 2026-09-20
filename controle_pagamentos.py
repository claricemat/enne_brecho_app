"""Aba "Controle de pagamentos (peças)" da página de Compras.

Mostra o que falta pagar às fornecedoras (a pagar / em atraso / total pago no
período) e permite dar baixa nas compras escolhendo a fornecedora, aplicando
desconto e combinando até duas formas de pagamento (conta bancária, caixa ou
crédito na loja).
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import streamlit as st

from db import transacao
from exportacao_compras import gerar_excel_compras, gerar_pdf_compras
from formatacao import brl as _brl
from formatacao import fmt_data as _fmt_data

# O servidor do Streamlit Cloud roda em UTC: à noite, date.today() já seria
# "amanhã" no Brasil e uma compra que vence hoje apareceria como em atraso.
_FUSO_BRASIL = timezone(timedelta(hours=-3))


class ComprasJaPagasError(Exception):
    """Alguma compra selecionada já foi paga (por exemplo, pela outra sócia)."""


# ----------------------------------------------------------------
# Helpers de formatação e conversão
# ----------------------------------------------------------------
def _hoje():
    return datetime.now(_FUSO_BRASIL).date()


def _dec(valor):
    """float do number_input -> Decimal com 2 casas (evita 0.1 + 0.2 = 0.30000000000000004)."""
    return Decimal(str(round(float(valor), 2)))


# ----------------------------------------------------------------
# Leitura (tudo numa conexão só, porque as abas do Streamlit rodam a cada interação)
# ----------------------------------------------------------------
def _carregar_dados(hoje, inicio, fim):
    with transacao() as q:
        resumo = q(
            """
            SELECT
                (SELECT COALESCE(SUM(valor_total), 0) FROM compra
                  WHERE status = 'pendente' AND data_vencimento BETWEEN %s AND %s
                    AND data_vencimento >= %s) AS a_pagar,
                (SELECT COALESCE(SUM(valor_total), 0) FROM compra
                  WHERE status = 'pendente' AND data_vencimento BETWEEN %s AND %s
                    AND data_vencimento < %s) AS em_atraso,
                (SELECT COALESCE(SUM(valor_total), 0) FROM compra
                  WHERE status = 'pendente' AND data_vencimento < %s
                    AND data_vencimento NOT BETWEEN %s AND %s) AS atraso_fora_periodo,
                (SELECT COALESCE(SUM(valor_pago), 0) FROM baixa_compra
                  WHERE data_pagamento BETWEEN %s AND %s)
                + (SELECT COALESCE(SUM(valor_total), 0) FROM compra
                    WHERE status = 'pago' AND baixa_id IS NULL
                      AND data_pagamento BETWEEN %s AND %s) AS total_pago,
                (SELECT COALESCE(SUM(desconto), 0) FROM baixa_compra
                  WHERE data_pagamento BETWEEN %s AND %s) AS descontos
            """,
            (
                inicio, fim, hoje,
                inicio, fim, hoje,
                hoje, inicio, fim,
                inicio, fim,
                inicio, fim,
                inicio, fim,
            ),
            fetch=True,
        )[0]

        pendentes = q(
            """
            SELECT c.id, c.fornecedora_id,
                   COALESCE(f.nome, '— sem fornecedora —') AS fornecedora,
                   tc.nome AS tipo_compra, c.data_aceite, c.data_vencimento, c.valor_total
            FROM compra c
            JOIN tipo_compra tc ON tc.id = c.tipo_compra_id
            LEFT JOIN fornecedora f ON f.id = c.fornecedora_id
            WHERE c.status = 'pendente'
            ORDER BY f.nome NULLS LAST, c.data_vencimento NULLS LAST, c.id
            """,
            fetch=True,
        )

        creditos = q(
            """
            SELECT fornecedora_id,
                   SUM(CASE WHEN tipo = 'entrada' THEN valor ELSE -valor END) AS saldo
            FROM credito_loja_movimento
            GROUP BY fornecedora_id
            """,
            fetch=True,
        )

        contas = q("SELECT id, nome, tipo FROM conta_financeira ORDER BY tipo, nome", fetch=True)

        compras_periodo = q(
            """
            SELECT c.id, COALESCE(f.nome, '—') AS fornecedora, tc.nome AS tipo_compra,
                   c.data_aceite, c.data_vencimento, c.status, c.data_pagamento, c.valor_total,
                   -- pagas com desconto: rateia o desconto da baixa entre as compras dela
                   CASE WHEN c.baixa_id IS NULL THEN c.valor_total
                        WHEN b.valor_bruto = 0 THEN 0
                        ELSE c.valor_total * b.valor_pago / b.valor_bruto END AS valor_liquido
            FROM compra c
            JOIN tipo_compra tc ON tc.id = c.tipo_compra_id
            LEFT JOIN fornecedora f ON f.id = c.fornecedora_id
            LEFT JOIN baixa_compra b ON b.id = c.baixa_id
            WHERE (c.status = 'pendente' AND c.data_vencimento BETWEEN %s AND %s)
               OR (c.status = 'pago' AND c.data_pagamento BETWEEN %s AND %s)
            """,
            (inicio, fim, inicio, fim),
            fetch=True,
        )

        historico = q(
            """
            SELECT b.id, b.data_pagamento, COALESCE(f.nome, '—') AS fornecedora,
                   (SELECT string_agg('#' || c.id, ', ' ORDER BY c.id)
                      FROM compra c WHERE c.baixa_id = b.id) AS compras,
                   b.valor_bruto, b.desconto, b.valor_pago,
                   (SELECT string_agg(
                               COALESCE(cf.nome, 'Crédito na loja') || ' R$ ' || bf.valor::text,
                               ' + ' ORDER BY bf.id)
                      FROM baixa_compra_forma bf
                      LEFT JOIN conta_financeira cf ON cf.id = bf.conta_financeira_id
                     WHERE bf.baixa_id = b.id) AS formas,
                   b.criado_por
            FROM baixa_compra b
            LEFT JOIN fornecedora f ON f.id = b.fornecedora_id
            WHERE b.data_pagamento BETWEEN %s AND %s
            ORDER BY b.data_pagamento DESC, b.id DESC
            """,
            (inicio, fim),
            fetch=True,
        )

    return {
        "resumo": resumo,
        "pendentes": pendentes,
        "creditos": {c["fornecedora_id"]: c["saldo"] for c in creditos},
        "contas": contas,
        "historico": historico,
        "compras_periodo": compras_periodo,
    }


# ----------------------------------------------------------------
# Escrita (transação única: ou grava tudo, ou nada)
# ----------------------------------------------------------------
def _registrar_pagamento(fornecedora_id, ids_compras, data_pagamento, valor_bruto,
                         desconto, formas, observacao, usuario):
    with transacao() as executar:
        # trava as compras e confere que ninguém pagou enquanto esta tela estava aberta
        travadas = executar(
            "SELECT id FROM compra WHERE id = ANY(%s) AND status = 'pendente' FOR UPDATE",
            (ids_compras,),
            fetch=True,
        )
        if len(travadas) != len(ids_compras):
            raise ComprasJaPagasError(
                "Alguma das compras selecionadas já foi paga (talvez pela outra pessoa). "
                "Atualize a página e confira antes de pagar de novo."
            )

        baixa = executar(
            """
            INSERT INTO baixa_compra
                (fornecedora_id, data_pagamento, valor_bruto, desconto, observacao, criado_por)
            VALUES (%s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (fornecedora_id, data_pagamento, valor_bruto, desconto, observacao or None, usuario),
            fetch=True,
        )
        baixa_id = baixa[0]["id"]

        for forma, conta_id, valor in formas:
            executar(
                "INSERT INTO baixa_compra_forma (baixa_id, forma, conta_financeira_id, valor) "
                "VALUES (%s, %s, %s, %s)",
                (baixa_id, forma, conta_id, valor),
            )
            if forma == "credito_loja":
                executar(
                    """
                    INSERT INTO credito_loja_movimento
                        (fornecedora_id, tipo, valor, data, baixa_id, observacao)
                    VALUES (%s, 'entrada', %s, %s, %s, %s)
                    """,
                    (fornecedora_id, valor, data_pagamento, baixa_id,
                     f"Pagamento das compras {', '.join('#' + str(i) for i in ids_compras)}"),
                )

        executar(
            "UPDATE compra SET status = 'pago', data_pagamento = %s, baixa_id = %s "
            "WHERE id = ANY(%s)",
            (data_pagamento, baixa_id, ids_compras),
        )
    return baixa_id


# ----------------------------------------------------------------
# Tela
# ----------------------------------------------------------------
def renderizar_controle_pagamentos():
    hoje = _hoje()
    st.caption(
        "Acompanhe o que falta pagar às fornecedoras e dê baixa nas compras, com "
        "desconto e até duas formas de pagamento."
    )

    mensagem = st.session_state.pop("pag_sucesso", None)
    if mensagem:
        st.success(mensagem)

    # ---- período ----
    primeiro_dia = hoje.replace(day=1)
    ultimo_dia = (primeiro_dia + timedelta(days=32)).replace(day=1) - timedelta(days=1)
    periodo = st.date_input(
        "Período", value=(primeiro_dia, ultimo_dia), format="DD/MM/YYYY", key="pag_periodo",
        help="Vale para os cartões, a tabela e o histórico: contas a pagar/em atraso pelo "
             "vencimento, valores pagos pela data do pagamento.",
    )
    if not isinstance(periodo, (tuple, list)) or len(periodo) != 2:
        st.info("Selecione a data inicial e a final do período.")
        return
    inicio, fim = periodo

    dados = _carregar_dados(hoje, inicio, fim)
    resumo = dados["resumo"]

    # ---- cartões (o botão de cada um filtra a tabela logo abaixo) ----
    col1, col2, col3 = st.columns(3)
    with col1.container(border=True):
        st.metric("A pagar (em aberto)", _brl(resumo["a_pagar"]),
                  help="Compras pendentes com vencimento no período que ainda não venceram.")
        _botao_visao("aberto")
    with col2.container(border=True):
        st.metric("Em atraso", _brl(resumo["em_atraso"]),
                  help="Compras pendentes com vencimento no período que já venceram.")
        _botao_visao("atraso")
    with col3.container(border=True):
        st.metric("Total pago", _brl(resumo["total_pago"]),
                  help="Já com desconto, incluindo compras à vista (bazar etc.).")
        if resumo["descontos"] > 0:
            st.caption(f"Descontos obtidos: {_brl(resumo['descontos'])}")
        _botao_visao("pago")

    if resumo["atraso_fora_periodo"] > 0:
        st.warning(
            f"Há {_brl(resumo['atraso_fora_periodo'])} em atraso com vencimento "
            "fora do período selecionado."
        )

    # ---- tabela de compras do período ----
    st.divider()
    _tabela_compras(dados["compras_periodo"], hoje, inicio, fim)

    # ---- pagar fornecedora ----
    st.divider()
    st.subheader("Pagar fornecedora")
    st.caption("Aqui aparecem todas as compras em aberto, independentemente do período acima.")

    if not dados["pendentes"]:
        st.success("Nenhuma compra em aberto. Tudo pago!")
    else:
        _formulario_pagamento(dados, hoje)

    # ---- histórico ----
    st.divider()
    st.subheader("Pagamentos do período")
    if dados["historico"]:
        st.dataframe(dados["historico"], use_container_width=True, hide_index=True)
    else:
        st.info("Nenhum pagamento registrado por aqui nesse período.")


# ----------------------------------------------------------------
# Tabela de compras (conversa com o período e com os cartões)
# ----------------------------------------------------------------
_VISOES = {
    "aberto": ("A pagar", "A pagar (em aberto)"),
    "atraso": ("Em atraso", "Em atraso"),
    "pago": ("Pago", "Pagas"),
}


def _alternar_visao(chave):
    """Clicar no botão do cartão filtra a tabela; clicar de novo limpa o filtro."""
    atual = st.session_state.get("pag_visao")
    st.session_state["pag_visao"] = None if atual == chave else chave


def _botao_visao(chave):
    ativo = st.session_state.get("pag_visao") == chave
    st.button(
        "Filtrando a tabela — limpar" if ativo else "Ver compras",
        key=f"pag_ver_{chave}",
        type="primary" if ativo else "secondary",
        use_container_width=True,
        on_click=_alternar_visao,
        args=(chave,),
    )


def _situacao(compra, hoje):
    if compra["status"] == "pago":
        return "Pago"
    if compra["data_vencimento"] and compra["data_vencimento"] < hoje:
        return "Em atraso"
    return "A pagar"


def _linhas_tabela(compras_periodo, hoje, visao):
    """Monta as linhas da tabela, filtradas pelo cartão escolhido e ordenadas
    do maior para o menor valor."""
    linhas = []
    for c in compras_periodo:
        situacao = _situacao(c, hoje)
        if visao and _VISOES[visao][0] != situacao:
            continue
        valor = c["valor_liquido"]
        linhas.append({
            "id": c["id"],
            "fornecedora": c["fornecedora"],
            "tipo_compra": c["tipo_compra"],
            "data_aceite": c["data_aceite"],
            "data_vencimento": c["data_vencimento"],
            "situacao": situacao,
            "data_pagamento": c["data_pagamento"] if situacao == "Pago" else None,
            "valor": valor,
            "desconto": (c["valor_total"] - valor) if situacao == "Pago" else Decimal("0"),
        })
    linhas.sort(key=lambda l: (l["valor"], l["id"]), reverse=True)
    return linhas


def _tabela_compras(compras_periodo, hoje, inicio, fim):
    visao = st.session_state.get("pag_visao")
    rotulo_visao = _VISOES[visao][1] if visao else "Todas as situações"

    st.subheader("Compras do período")
    st.caption(
        f"{_fmt_data(inicio)} a {_fmt_data(fim)} — {rotulo_visao}. "
        "Use o botão \"Ver compras\" de um cartão para filtrar; ordenadas do maior para o menor valor."
    )

    linhas = _linhas_tabela(compras_periodo, hoje, visao)
    if not linhas:
        st.info("Nenhuma compra nesse filtro e período.")
        return

    exibicao = [
        {
            "Compra": f"#{l['id']}",
            "Fornecedora": l["fornecedora"],
            "Tipo de compra": l["tipo_compra"],
            "Data da compra": _fmt_data(l["data_aceite"]),
            "Vencimento": _fmt_data(l["data_vencimento"]),
            "Situação": l["situacao"],
            "Pago em": _fmt_data(l["data_pagamento"]),
            "Valor": _brl(l["valor"]),
            "Desconto": _brl(l["desconto"]) if l["desconto"] > 0 else "—",
        }
        for l in linhas
    ]
    st.dataframe(exibicao, use_container_width=True, hide_index=True)

    # totais por situação (bate com o cartão quando há filtro)
    totais = []
    for situacao in ("A pagar", "Em atraso", "Pago"):
        subtotal = sum((l["valor"] for l in linhas if l["situacao"] == situacao), Decimal("0"))
        if any(l["situacao"] == situacao for l in linhas):
            totais.append((situacao, subtotal))
    st.caption(f"{len(linhas)} compra(s) — " + " · ".join(f"{s}: {_brl(t)}" for s, t in totais))

    periodo_txt = f"{_fmt_data(inicio)} a {_fmt_data(fim)}"
    nome_base = f"compras_{visao or 'todas'}_{inicio.isoformat()}_{fim.isoformat()}"
    with st.popover("Exportar"):
        st.download_button(
            "Baixar em Excel (.xlsx)",
            data=gerar_excel_compras(linhas, periodo_txt, rotulo_visao, totais),
            file_name=f"{nome_base}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True,
        )
        st.download_button(
            "Baixar em PDF",
            data=gerar_pdf_compras(linhas, periodo_txt, rotulo_visao, totais),
            file_name=f"{nome_base}.pdf",
            mime="application/pdf",
            use_container_width=True,
        )


def _formulario_pagamento(dados, hoje):
    versao = st.session_state.setdefault("pag_versao", 0)

    # agrupa as compras pendentes por fornecedora (mantém a ordem da consulta)
    por_fornecedora = {}
    for c in dados["pendentes"]:
        por_fornecedora.setdefault(c["fornecedora_id"], []).append(c)

    opcoes_fornecedora = {}
    for forn_id, compras in por_fornecedora.items():
        em_aberto = sum((c["valor_total"] for c in compras), Decimal("0"))
        em_atraso = sum(
            (c["valor_total"] for c in compras if c["data_vencimento"] and c["data_vencimento"] < hoje),
            Decimal("0"),
        )
        rotulo = f"{compras[0]['fornecedora']} — {_brl(em_aberto)} em aberto"
        if em_atraso > 0:
            rotulo += f" ({_brl(em_atraso)} em atraso)"
        opcoes_fornecedora[rotulo] = forn_id

    escolha = st.selectbox("Fornecedora", list(opcoes_fornecedora), key=f"pag_forn_{versao}")
    fornecedora_id = opcoes_fornecedora[escolha]
    compras_forn = por_fornecedora[fornecedora_id]

    credito = dados["creditos"].get(fornecedora_id)
    if fornecedora_id is not None and credito and credito > 0:
        st.caption(f"Crédito na loja já concedido a ela: {_brl(credito)}")

    # ---- compras a pagar ----
    opcoes_compra = {}
    for c in compras_forn:
        rotulo = (
            f"Compra #{c['id']} — {c['tipo_compra']} — "
            f"venc. {_fmt_data(c['data_vencimento'])} — {_brl(c['valor_total'])}"
        )
        if c["data_vencimento"] and c["data_vencimento"] < hoje:
            rotulo += " (em atraso)"
        opcoes_compra[rotulo] = c["id"]

    selecionadas = st.multiselect(
        "Compras a pagar",
        options=list(opcoes_compra),
        default=list(opcoes_compra) if len(opcoes_compra) == 1 else [],
        key=f"pag_compras_{versao}_{fornecedora_id}",
    )
    if not selecionadas:
        st.info("Selecione ao menos uma compra para dar baixa.")
        return

    ids = [opcoes_compra[r] for r in selecionadas]
    valor_bruto = sum((c["valor_total"] for c in compras_forn if c["id"] in ids), Decimal("0"))
    chave = f"{versao}_{fornecedora_id}_{'-'.join(map(str, ids))}"

    # ---- data e desconto ----
    col_data, col_desc = st.columns(2)
    data_pagamento = col_data.date_input(
        "Data do pagamento", value=hoje, max_value=hoje, format="DD/MM/YYYY", key=f"pag_data_{chave}"
    )
    desconto = _dec(
        col_desc.number_input(
            "Desconto (R$)", min_value=0.0, max_value=float(valor_bruto), value=0.0,
            step=1.0, format="%.2f", key=f"pag_desc_{chave}",
        )
    )
    valor_a_pagar = valor_bruto - desconto

    m1, m2, m3 = st.columns(3)
    m1.metric("Total das compras", _brl(valor_bruto))
    m2.metric("Desconto", _brl(desconto))
    m3.metric("Total a pagar", _brl(valor_a_pagar))

    # ---- formas de pagamento ----
    formas_disponiveis = {}
    for conta in dados["contas"]:
        sufixo = "caixa" if conta["tipo"] == "caixa" else "conta bancária"
        formas_disponiveis[f"{conta['nome']} ({sufixo})"] = ("conta", conta["id"])
    if fornecedora_id is not None:
        formas_disponiveis["Crédito na loja"] = ("credito_loja", None)
    rotulos_forma = list(formas_disponiveis)

    if not any(c["tipo"] == "banco" for c in dados["contas"]):
        st.caption("Nenhuma conta bancária cadastrada ainda — cadastre em Cadastros → Contas e caixa.")

    formas_escolhidas = []
    erro = None
    chave_valor = f"{chave}_{desconto}"

    if valor_a_pagar == 0:
        st.info("Com esse desconto o pagamento fica zerado — nenhuma forma de pagamento necessária.")
    else:
        dividir = st.checkbox(
            "Dividir em duas formas de pagamento",
            key=f"pag_dividir_{chave_valor}",
            disabled=valor_a_pagar < Decimal("0.02"),
        )
        if not dividir:
            r1 = st.selectbox("Forma de pagamento", rotulos_forma, key=f"pag_forma1_{chave_valor}")
            formas_escolhidas = [(*formas_disponiveis[r1], valor_a_pagar)]
        else:
            col_a, col_b = st.columns(2)
            r1 = col_a.selectbox("Forma 1", rotulos_forma, key=f"pag_forma1_{chave_valor}")
            valor1 = _dec(
                col_a.number_input(
                    "Valor na forma 1 (R$)",
                    min_value=0.01,
                    max_value=float(valor_a_pagar - Decimal("0.01")),
                    value=float((valor_a_pagar / 2).quantize(Decimal("0.01"))),
                    step=1.0, format="%.2f", key=f"pag_valor1_{chave_valor}",
                )
            )
            r2 = col_b.selectbox(
                "Forma 2", rotulos_forma, index=min(1, len(rotulos_forma) - 1),
                key=f"pag_forma2_{chave_valor}",
            )
            valor2 = valor_a_pagar - valor1
            col_b.metric("Valor na forma 2 (restante)", _brl(valor2))
            if formas_disponiveis[r1] == formas_disponiveis[r2]:
                erro = "Escolha duas formas de pagamento diferentes."
            formas_escolhidas = [
                (*formas_disponiveis[r1], valor1),
                (*formas_disponiveis[r2], valor2),
            ]

        if any(f[0] == "credito_loja" for f in formas_escolhidas):
            st.caption(
                "Pagar em crédito na loja não movimenta dinheiro: o valor vira crédito "
                "da fornecedora para usar em compras na loja."
            )

    observacao = st.text_input("Observação (opcional)", key=f"pag_obs_{chave}")

    if erro:
        st.error(erro)

    if st.button("Confirmar pagamento", type="primary", disabled=bool(erro), key=f"pag_confirmar_{chave_valor}"):
        try:
            _registrar_pagamento(
                fornecedora_id, ids, data_pagamento, valor_bruto, desconto,
                formas_escolhidas, observacao.strip(), st.session_state.get("usuario"),
            )
        except ComprasJaPagasError as e:
            st.error(str(e))
            return
        except Exception as e:
            st.error(f"Não foi possível registrar o pagamento. Nada foi gravado. Erro: {e}")
            return

        st.session_state["pag_sucesso"] = (
            f"Pagamento registrado: {len(ids)} compra(s), {_brl(valor_a_pagar)} pagos"
            + (f" (desconto de {_brl(desconto)})" if desconto > 0 else "")
            + "."
        )
        st.session_state["pag_versao"] = versao + 1
        st.rerun()
