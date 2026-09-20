from datetime import date, timedelta

import pandas as pd
import streamlit as st

from db import run_query, transacao
from branding import aplicar_logo
from auth import exigir_login, botao_logout
from pdf_avaliacao import gerar_pdf_avaliacao
from controle_pagamentos import renderizar_controle_pagamentos
from formatacao import brl, fmt_data
from prazos_proposta import (
    PRAZO_CURTO_DIAS,
    PRAZO_LONGO_DIAS_UTEIS,
    ROTULO_CURTO,
    ROTULO_LONGO,
    vencimento_da_proposta,
)

st.set_page_config(page_title="Compras", page_icon="assets/icone_coracao.png", layout="wide")
aplicar_logo()
exigir_login()
botao_logout()
st.title("Compras")

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

nomes_tipo_peca = [t["nome"] for t in tipos_peca]
tipo_peca_por_nome = {t["nome"]: t["id"] for t in tipos_peca}

def _limpar_itens_avaliacao(df, tipo_padrao):
    """Corrige células que o data_editor deixa em branco (NaN) em linhas
    novas — sem isso, bool(NaN) vira True e derruba a lógica de aprovação."""
    limpo = df[df["descricao"].fillna("").str.strip() != ""].copy()
    limpo["aprovada"] = limpo["aprovada"].fillna(False).astype(bool)
    limpo["valor_curto"] = pd.to_numeric(limpo["valor_curto"], errors="coerce").fillna(0.0)
    limpo["valor_longo"] = pd.to_numeric(limpo["valor_longo"], errors="coerce").fillna(0.0)
    limpo["tipo_peca"] = limpo["tipo_peca"].fillna(tipo_padrao)
    limpo["tamanho"] = limpo["tamanho"].fillna("")
    limpo["observacao"] = limpo["observacao"].fillna("")
    return limpo


def _aprovadas_sem_valor(itens_df):
    """Peças aprovadas precisam ter valor nas duas propostas."""
    aprovadas = itens_df[itens_df["aprovada"]]
    return int(((aprovadas["valor_curto"] <= 0) | (aprovadas["valor_longo"] <= 0)).sum())


def _salvar_itens_avaliacao(executar, avaliacao_id, itens_df, tipo_peca_por_nome):
    """Apaga os itens atuais da avaliação e grava os do dataframe editado
    (dentro da transação recebida). Retorna a lista pronta pra gerar o PDF."""
    executar("DELETE FROM avaliacao_item WHERE avaliacao_id = %s", (avaliacao_id,))
    itens_para_pdf = []
    for _, item in itens_df.iterrows():
        aprovada = bool(item["aprovada"])
        curto = float(item["valor_curto"]) if aprovada else None
        longo = float(item["valor_longo"]) if aprovada else None
        executar(
            """
            INSERT INTO avaliacao_item
                (avaliacao_id, descricao, tipo_peca_id, tamanho, aprovada,
                 valor_curto_prazo, valor_longo_prazo, observacao)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                avaliacao_id,
                item["descricao"],
                tipo_peca_por_nome.get(item["tipo_peca"]),
                item["tamanho"] or None,
                aprovada,
                curto,
                longo,
                item["observacao"] or None,
            ),
        )
        itens_para_pdf.append(
            {
                "descricao": item["descricao"],
                "tipo_peca": item["tipo_peca"],
                "tamanho": item["tamanho"],
                "aprovada": aprovada,
                "valor_curto_prazo": curto,
                "valor_longo_prazo": longo,
                "observacao": item["observacao"],
            }
        )
    return itens_para_pdf


def _config_colunas_avaliacao():
    return {
        "descricao": st.column_config.TextColumn("Descrição"),
        "tipo_peca": st.column_config.SelectboxColumn("Tipo de peça", options=nomes_tipo_peca),
        "tamanho": st.column_config.TextColumn("Tamanho"),
        "aprovada": st.column_config.CheckboxColumn("Aprovada?"),
        "valor_curto": st.column_config.NumberColumn(
            "Proposta A — curto prazo (R$)", min_value=0.0, step=1.0,
            help=f"Valor se o pagamento for feito em até {PRAZO_CURTO_DIAS} dias. Só vale se a peça for aprovada.",
        ),
        "valor_longo": st.column_config.NumberColumn(
            "Proposta B — longo prazo (R$)", min_value=0.0, step=1.0,
            help=f"Valor se o pagamento for feito em até {PRAZO_LONGO_DIAS_UTEIS} dias úteis. Só vale se a peça for aprovada.",
        ),
        "observacao": st.column_config.TextColumn("Observação"),
    }


aba_compra, aba_avaliacao, aba_pagamentos = st.tabs(
    ["Registrar Compra", "Avaliação de Peças", "Controle de pagamentos (peças)"]
)

# ================================================================
# Aba: Registrar Compra
# ================================================================
with aba_compra:
    opcoes_tipo_compra = {t["nome"]: t for t in tipos_compra}
    nome_tipo_compra = st.selectbox(
        "Tipo de compra", options=list(opcoes_tipo_compra.keys()), key="tipo_compra_sel"
    )
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

    # ---- propostas em aberto dessa fornecedora ----
    versao_compra = st.session_state.setdefault("compra_version", 0)
    avaliacao_selecionada_id = None
    proposta_escolhida = None  # 'curto' ou 'longo' (só quando usa uma avaliação)
    itens_iniciais = pd.DataFrame(
        [{"descricao": "", "tipo_peca": nomes_tipo_peca[0], "tamanho": "", "preco_venda": 0.0, "preco_custo": 0.0}]
    )

    if fornecedora_id:
        propostas_abertas = run_query(
            """
            SELECT a.id, a.data_avaliacao,
                   COUNT(*) FILTER (WHERE ai.aprovada) AS aprovadas,
                   COALESCE(SUM(ai.valor_curto_prazo) FILTER (WHERE ai.aprovada), 0) AS total_curto,
                   COALESCE(SUM(ai.valor_longo_prazo) FILTER (WHERE ai.aprovada), 0) AS total_longo
            FROM avaliacao a
            JOIN avaliacao_item ai ON ai.avaliacao_id = a.id
            WHERE a.fornecedora_id = %s AND a.status = 'pendente'
            GROUP BY a.id, a.data_avaliacao
            HAVING COUNT(*) FILTER (WHERE ai.aprovada) > 0
            ORDER BY a.data_avaliacao DESC
            """,
            (fornecedora_id,),
        )
        if propostas_abertas:
            opcoes_proposta = {"— nenhuma / compra manual —": None}
            for p in propostas_abertas:
                rotulo = (
                    f"Avaliação #{p['id']} — {fmt_data(p['data_avaliacao'])} — "
                    f"{p['aprovadas']} peça(s) — A: {brl(p['total_curto'])} · B: {brl(p['total_longo'])}"
                )
                opcoes_proposta[rotulo] = p["id"]
            escolha_proposta = st.selectbox(
                "Usar avaliação em aberto dessa fornecedora (opcional)",
                options=list(opcoes_proposta.keys()),
            )
            avaliacao_selecionada_id = opcoes_proposta[escolha_proposta]

            if avaliacao_selecionada_id:
                opcoes_prazo = {ROTULO_CURTO: "curto", ROTULO_LONGO: "longo"}
                escolha_prazo = st.radio(
                    "Proposta aceita pela fornecedora",
                    options=list(opcoes_prazo.keys()),
                    key=f"proposta_aceita_{avaliacao_selecionada_id}",
                    help="Só uma das duas propostas é fechada. Ela define o custo das peças e o vencimento.",
                )
                proposta_escolhida = opcoes_prazo[escolha_prazo]

                itens_aprovados = run_query(
                    """
                    SELECT ai.descricao, COALESCE(tp.nome, %s) AS tipo_peca, ai.tamanho,
                           ai.valor_curto_prazo, ai.valor_longo_prazo
                    FROM avaliacao_item ai
                    LEFT JOIN tipo_peca tp ON tp.id = ai.tipo_peca_id
                    WHERE ai.avaliacao_id = %s AND ai.aprovada = true
                    ORDER BY ai.id
                    """,
                    (nomes_tipo_peca[0], avaliacao_selecionada_id),
                )
                coluna_valor = "valor_curto_prazo" if proposta_escolhida == "curto" else "valor_longo_prazo"
                itens_iniciais = pd.DataFrame(
                    [
                        {
                            "descricao": i["descricao"],
                            "tipo_peca": i["tipo_peca"],
                            "tamanho": i["tamanho"] or "",
                            "preco_venda": 0.0,
                            "preco_custo": float(i[coluna_valor]),
                        }
                        for i in itens_aprovados
                    ]
                )
                st.info(
                    "Peças da avaliação carregadas abaixo com o custo da proposta escolhida — "
                    "falta só definir o preço de venda de cada uma."
                )

    if proposta_escolhida:
        st.caption("Pagamento conforme a proposta aceita; o vencimento sugerido pode ser ajustado abaixo.")
    elif tipo_selecionado["prazo_dias"] > 0:
        st.caption(f"Prazo de pagamento: {tipo_selecionado['prazo_dias']} dias após o aceite.")
    else:
        st.caption("Compra à vista — já entra como paga na data de hoje.")

    data_aceite = st.date_input("Data da compra", value=date.today(), format="DD/MM/YYYY")

    # ---- vencimento (sugerido pela proposta ou pelo tipo de compra; dá pra ajustar) ----
    if proposta_escolhida:
        vencimento_sugerido = vencimento_da_proposta(proposta_escolhida, data_aceite)
    elif tipo_selecionado["prazo_dias"] > 0:
        vencimento_sugerido = data_aceite + timedelta(days=tipo_selecionado["prazo_dias"])
    else:
        vencimento_sugerido = None

    data_vencimento = None
    if vencimento_sugerido:
        data_vencimento = st.date_input(
            "Data de vencimento",
            value=vencimento_sugerido,
            min_value=data_aceite,
            format="DD/MM/YYYY",
            key=f"venc_compra_{versao_compra}_{vencimento_sugerido}",
        )

    st.subheader("Peças do lote")
    st.caption(
        "Preencha uma linha por peça, com o custo real pago. Use o + no fim da "
        "tabela pra adicionar linhas."
    )

    itens = st.data_editor(
        itens_iniciais,
        num_rows="dynamic",
        use_container_width=True,
        key=f"editor_compra_{versao_compra}_{avaliacao_selecionada_id or 'manual'}_{proposta_escolhida or ''}",
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
        if data_vencimento is not None:
            status = "pendente"
            data_pagamento = None
        else:
            status = "pago"
            data_pagamento = data_aceite

        try:
            # tudo numa transação: se algo falhar no meio, nenhuma peça fica pela metade
            with transacao() as executar:
                compra = executar(
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
                    fetch=True,
                )
                compra_id = compra[0]["id"]

                for _, item in itens_validos.iterrows():
                    produto = executar(
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
                        fetch=True,
                    )
                    executar(
                        "INSERT INTO movimentacao_estoque (produto_id, tipo, observacao) "
                        "VALUES (%s, 'entrada', %s)",
                        (produto[0]["id"], f"Entrada via compra #{compra_id} ({nome_tipo_compra})"),
                    )

                if avaliacao_selecionada_id:
                    aceita = executar(
                        """
                        UPDATE avaliacao
                        SET status = 'aceita', compra_id = %s, proposta_aceita = %s
                        WHERE id = %s AND status = 'pendente'
                        RETURNING id
                        """,
                        (compra_id, proposta_escolhida, avaliacao_selecionada_id),
                        fetch=True,
                    )
                    if not aceita:
                        raise ValueError(
                            "Essa avaliação já foi aceita ou recusada (talvez pela outra pessoa). "
                            "Nada foi gravado — atualize a página."
                        )
        except ValueError as e:
            st.error(str(e))
        except Exception as e:
            st.error(f"Não foi possível registrar a compra. Nada foi gravado. Erro: {e}")
        else:
            st.session_state["compra_sucesso"] = (
                f"Compra #{compra_id} registrada com {len(itens_validos)} peça(s), "
                f"total R$ {valor_total:.2f}"
                + (f", vence em {fmt_data(data_vencimento)}." if data_vencimento else ".")
            )
            st.session_state.compra_version += 1
            st.rerun()

    mensagem_compra = st.session_state.pop("compra_sucesso", None)
    if mensagem_compra:
        st.success(mensagem_compra)

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
        st.caption("Pra pagar uma compra em aberto, use a aba Controle de pagamentos (peças).")
    else:
        st.info("Nenhuma compra registrada ainda.")

    # ---- editar vencimento de compras em aberto ----
    compras_em_aberto = run_query(
        """
        SELECT c.id, COALESCE(f.nome, '—') AS fornecedora, c.data_aceite, c.data_vencimento, c.valor_total
        FROM compra c
        LEFT JOIN fornecedora f ON f.id = c.fornecedora_id
        WHERE c.status = 'pendente'
        ORDER BY c.data_vencimento NULLS LAST, c.id
        """
    )
    if compras_em_aberto:
        st.subheader("Editar data de vencimento")
        st.caption("Só compras em aberto. Compras já pagas não têm o vencimento alterado.")

        mensagem_venc = st.session_state.pop("venc_sucesso", None)
        if mensagem_venc:
            st.success(mensagem_venc)

        opcoes_venc = {
            f"Compra #{c['id']} — {c['fornecedora']} — {brl(c['valor_total'])} — "
            f"vence {fmt_data(c['data_vencimento'])}": c
            for c in compras_em_aberto
        }
        escolha_venc = st.selectbox("Compra", options=list(opcoes_venc.keys()), key="venc_edit_sel")
        compra_venc = opcoes_venc[escolha_venc]
        limite_inferior = min(d for d in (compra_venc["data_aceite"], compra_venc["data_vencimento"]) if d)
        novo_vencimento = st.date_input(
            "Novo vencimento",
            value=compra_venc["data_vencimento"] or compra_venc["data_aceite"],
            min_value=limite_inferior,
            format="DD/MM/YYYY",
            key=f"venc_edit_{compra_venc['id']}_{compra_venc['data_vencimento']}",
        )
        if st.button(
            "Salvar vencimento",
            key="venc_edit_salvar",
            disabled=novo_vencimento == compra_venc["data_vencimento"],
        ):
            atualizada = run_query(
                "UPDATE compra SET data_vencimento = %s WHERE id = %s AND status = 'pendente' RETURNING id",
                (novo_vencimento, compra_venc["id"]),
            )
            if atualizada:
                st.session_state["venc_sucesso"] = (
                    f"Vencimento da compra #{compra_venc['id']} alterado para {fmt_data(novo_vencimento)}."
                )
                st.rerun()
            else:
                st.error("Essa compra já foi paga — o vencimento não pode mais ser alterado.")

# ================================================================
# Aba: Avaliação de Peças
# ================================================================
with aba_avaliacao:
    st.caption(
        "Registre o lote de peças trazido pela fornecedora pra avaliação, aprove "
        "ou reprove cada peça, e gere o PDF da proposta pra enviar a ela."
    )

    if "editando_avaliacao_id" not in st.session_state:
        st.session_state.editando_avaliacao_id = None

    fornecedoras_aval = run_query("SELECT id, nome FROM fornecedora ORDER BY nome")
    if not fornecedoras_aval:
        st.warning("Cadastre uma fornecedora antes de criar uma avaliação.")
    else:
        opcoes_fornecedora_aval = {f["nome"]: f["id"] for f in fornecedoras_aval}

        # ---------------------------------------------------------
        # Modo edição — só pra avaliações ainda pendentes
        # ---------------------------------------------------------
        if st.session_state.editando_avaliacao_id:
            aval_id_edicao = st.session_state.editando_avaliacao_id
            aval_atual = run_query(
                """
                SELECT a.id, a.fornecedora_id, f.nome AS fornecedora, a.data_avaliacao, a.status
                FROM avaliacao a JOIN fornecedora f ON f.id = a.fornecedora_id
                WHERE a.id = %s
                """,
                (aval_id_edicao,),
            )
            if not aval_atual or aval_atual[0]["status"] != "pendente":
                st.warning("Essa avaliação não está mais pendente — não dá mais pra editar.")
                st.session_state.editando_avaliacao_id = None
                st.rerun()
            aval_atual = aval_atual[0]

            st.subheader(f"Editando avaliação #{aval_id_edicao}")

            nomes_fornecedora_lista = list(opcoes_fornecedora_aval.keys())
            indice_fornecedora = (
                nomes_fornecedora_lista.index(aval_atual["fornecedora"])
                if aval_atual["fornecedora"] in nomes_fornecedora_lista
                else 0
            )
            nome_fornecedora_edicao = st.selectbox(
                "Fornecedora", options=nomes_fornecedora_lista, index=indice_fornecedora, key="fornecedora_edicao"
            )
            data_avaliacao_edicao = st.date_input(
                "Data da avaliação", value=aval_atual["data_avaliacao"], key="data_edicao"
            )

            itens_atuais = run_query(
                """
                SELECT ai.descricao, COALESCE(tp.nome, %s) AS tipo_peca, ai.tamanho,
                       ai.aprovada, ai.valor_curto_prazo, ai.valor_longo_prazo, ai.observacao
                FROM avaliacao_item ai
                LEFT JOIN tipo_peca tp ON tp.id = ai.tipo_peca_id
                WHERE ai.avaliacao_id = %s
                ORDER BY ai.id
                """,
                (nomes_tipo_peca[0], aval_id_edicao),
            )
            df_edicao = pd.DataFrame(
                [
                    {
                        "descricao": i["descricao"],
                        "tipo_peca": i["tipo_peca"],
                        "tamanho": i["tamanho"] or "",
                        "aprovada": i["aprovada"],
                        "valor_curto": float(i["valor_curto_prazo"]) if i["valor_curto_prazo"] is not None else 0.0,
                        "valor_longo": float(i["valor_longo_prazo"]) if i["valor_longo_prazo"] is not None else 0.0,
                        "observacao": i["observacao"] or "",
                    }
                    for i in itens_atuais
                ]
            )

            itens_editados = st.data_editor(
                df_edicao,
                num_rows="dynamic",
                use_container_width=True,
                key=f"editor_edicao_{aval_id_edicao}",
                column_config=_config_colunas_avaliacao(),
            )
            itens_editados_limpos = _limpar_itens_avaliacao(itens_editados, nomes_tipo_peca[0])
            sem_valor_edicao = _aprovadas_sem_valor(itens_editados_limpos)
            if sem_valor_edicao:
                st.warning(
                    f"{sem_valor_edicao} peça(s) aprovada(s) sem valor em uma das propostas — "
                    "preencha as duas para salvar."
                )

            col_salvar, col_cancelar = st.columns(2)
            if col_salvar.button(
                "Salvar alterações",
                type="primary",
                disabled=itens_editados_limpos.empty or bool(sem_valor_edicao),
            ):
                with transacao() as executar:
                    executar(
                        "UPDATE avaliacao SET fornecedora_id = %s, data_avaliacao = %s WHERE id = %s",
                        (opcoes_fornecedora_aval[nome_fornecedora_edicao], data_avaliacao_edicao, aval_id_edicao),
                    )
                    itens_para_pdf = _salvar_itens_avaliacao(
                        executar, aval_id_edicao, itens_editados_limpos, tipo_peca_por_nome
                    )
                st.success(f"Avaliação #{aval_id_edicao} atualizada.")
                pdf_bytes = gerar_pdf_avaliacao(nome_fornecedora_edicao, data_avaliacao_edicao, itens_para_pdf)
                st.download_button(
                    "Baixar PDF atualizado (propostas A e B)",
                    data=pdf_bytes,
                    file_name=f"proposta_avaliacao_{aval_id_edicao}.pdf",
                    mime="application/pdf",
                    key=f"pdf_pos_edicao_{aval_id_edicao}",
                )
                if st.button("Concluir edição"):
                    st.session_state.editando_avaliacao_id = None
                    st.rerun()
            if col_cancelar.button("Cancelar edição"):
                st.session_state.editando_avaliacao_id = None
                st.rerun()

        # ---------------------------------------------------------
        # Modo criação — só aparece quando não está editando
        # ---------------------------------------------------------
        else:
            nome_fornecedora_aval = st.selectbox(
                "Fornecedora", options=list(opcoes_fornecedora_aval.keys()), key="fornecedora_aval_sel"
            )
            data_avaliacao = st.date_input("Data da avaliação", value=date.today(), key="data_aval")

            if "avaliacao_version" not in st.session_state:
                st.session_state.avaliacao_version = 0

            itens_avaliacao = st.data_editor(
                pd.DataFrame(
                    [
                        {
                            "descricao": "",
                            "tipo_peca": nomes_tipo_peca[0],
                            "tamanho": "",
                            "aprovada": False,
                            "valor_curto": 0.0,
                            "valor_longo": 0.0,
                            "observacao": "",
                        }
                    ]
                ),
                num_rows="dynamic",
                use_container_width=True,
                key=f"editor_avaliacao_{st.session_state.avaliacao_version}",
                column_config=_config_colunas_avaliacao(),
            )
            st.caption(
                f"Cada peça aprovada recebe duas propostas: A — pagamento em até {PRAZO_CURTO_DIAS} dias "
                f"e B — pagamento em até {PRAZO_LONGO_DIAS_UTEIS} dias úteis. O PDF leva as duas, "
                "mas só uma será fechada."
            )

            itens_aval_validos = _limpar_itens_avaliacao(itens_avaliacao, nomes_tipo_peca[0])
            sem_valor = _aprovadas_sem_valor(itens_aval_validos)
            if sem_valor:
                st.warning(
                    f"{sem_valor} peça(s) aprovada(s) sem valor em uma das propostas — "
                    "preencha as duas para salvar."
                )

            if st.button("Salvar avaliação", type="primary", disabled=itens_aval_validos.empty or bool(sem_valor)):
                fornecedora_aval_id = opcoes_fornecedora_aval[nome_fornecedora_aval]

                with transacao() as executar:
                    avaliacao = executar(
                        "INSERT INTO avaliacao (fornecedora_id, data_avaliacao) VALUES (%s, %s) RETURNING id",
                        (fornecedora_aval_id, data_avaliacao),
                        fetch=True,
                    )
                    avaliacao_id = avaliacao[0]["id"]
                    itens_para_pdf = _salvar_itens_avaliacao(
                        executar, avaliacao_id, itens_aval_validos, tipo_peca_por_nome
                    )

                st.success(f"Avaliação #{avaliacao_id} salva com {len(itens_aval_validos)} peça(s).")

                pdf_bytes = gerar_pdf_avaliacao(nome_fornecedora_aval, data_avaliacao, itens_para_pdf)
                st.download_button(
                    "Baixar PDF das propostas (A e B)",
                    data=pdf_bytes,
                    file_name=f"proposta_avaliacao_{avaliacao_id}.pdf",
                    mime="application/pdf",
                )

        st.divider()
        st.subheader("Avaliações recentes")

        avaliacoes = run_query(
            """
            SELECT a.id, f.nome AS fornecedora, a.data_avaliacao, a.status,
                   CASE a.proposta_aceita WHEN 'curto' THEN 'A (curto prazo)'
                                          WHEN 'longo' THEN 'B (longo prazo)' END AS proposta_aceita,
                   COUNT(*) FILTER (WHERE ai.aprovada) AS aprovadas,
                   COUNT(*) FILTER (WHERE NOT ai.aprovada) AS reprovadas,
                   COALESCE(SUM(ai.valor_curto_prazo) FILTER (WHERE ai.aprovada), 0) AS total_proposta_a,
                   COALESCE(SUM(ai.valor_longo_prazo) FILTER (WHERE ai.aprovada), 0) AS total_proposta_b
            FROM avaliacao a
            JOIN fornecedora f ON f.id = a.fornecedora_id
            LEFT JOIN avaliacao_item ai ON ai.avaliacao_id = a.id
            GROUP BY a.id, f.nome, a.data_avaliacao, a.status, a.proposta_aceita
            ORDER BY a.data_avaliacao DESC, a.id DESC
            LIMIT 30
            """
        )

        if avaliacoes:
            st.dataframe(avaliacoes, use_container_width=True, hide_index=True)

            opcoes_aval = {
                f"Avaliação #{a['id']} — {a['fornecedora']} — {a['data_avaliacao']} ({a['status']})": a
                for a in avaliacoes
            }
            escolha_aval = st.selectbox("Selecionar avaliação", options=list(opcoes_aval.keys()))
            aval_selecionada = opcoes_aval[escolha_aval]

            col1, col2, col3 = st.columns(3)
            with col1:
                itens_aval_download = run_query(
                    """
                    SELECT ai.descricao, tp.nome AS tipo_peca, ai.tamanho, ai.aprovada,
                           ai.valor_curto_prazo, ai.valor_longo_prazo, ai.observacao
                    FROM avaliacao_item ai
                    LEFT JOIN tipo_peca tp ON tp.id = ai.tipo_peca_id
                    WHERE ai.avaliacao_id = %s
                    ORDER BY ai.id
                    """,
                    (aval_selecionada["id"],),
                )
                proposta_aceita_codigo = {"A (curto prazo)": "curto", "B (longo prazo)": "longo"}.get(
                    aval_selecionada["proposta_aceita"]
                )
                pdf_bytes_download = gerar_pdf_avaliacao(
                    aval_selecionada["fornecedora"],
                    aval_selecionada["data_avaliacao"],
                    itens_aval_download,
                    proposta_aceita_codigo,
                )
                st.download_button(
                    "Baixar PDF",
                    data=pdf_bytes_download,
                    file_name=f"proposta_avaliacao_{aval_selecionada['id']}.pdf",
                    mime="application/pdf",
                    key=f"pdf_{aval_selecionada['id']}",
                )
            with col2:
                if aval_selecionada["status"] == "pendente":
                    if st.button("Editar", key=f"editar_{aval_selecionada['id']}"):
                        st.session_state.editando_avaliacao_id = aval_selecionada["id"]
                        st.rerun()
            with col3:
                if aval_selecionada["status"] == "pendente":
                    if st.button("Marcar como recusada", key=f"recusar_{aval_selecionada['id']}"):
                        run_query(
                            "UPDATE avaliacao SET status = 'recusada' WHERE id = %s",
                            (aval_selecionada["id"],),
                            fetch=False,
                        )
                        st.success("Avaliação marcada como recusada.")
                        st.rerun()
        else:
            st.info("Nenhuma avaliação registrada ainda.")

# ================================================================
# Aba: Controle de pagamentos (peças)
# ================================================================
with aba_pagamentos:
    renderizar_controle_pagamentos()
