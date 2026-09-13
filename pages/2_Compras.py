from datetime import date, timedelta

import pandas as pd
import streamlit as st

from db import run_query
from branding import aplicar_logo
from auth import exigir_login, botao_logout
from pdf_avaliacao import gerar_pdf_avaliacao

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

aba_compra, aba_avaliacao = st.tabs(["Registrar Compra", "Avaliação de Peças"])

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

    if tipo_selecionado["prazo_dias"] > 0:
        st.caption(f"Prazo de pagamento: {tipo_selecionado['prazo_dias']} dias após o aceite.")
    else:
        st.caption("Compra à vista — já entra como paga na data de hoje.")

    # ---- propostas em aberto dessa fornecedora ----
    avaliacao_selecionada_id = None
    itens_iniciais = pd.DataFrame(
        [{"descricao": "", "tipo_peca": nomes_tipo_peca[0], "tamanho": "", "preco_venda": 0.0, "preco_custo": 0.0}]
    )

    if fornecedora_id:
        propostas_abertas = run_query(
            """
            SELECT a.id, a.data_avaliacao,
                   COUNT(*) FILTER (WHERE ai.aprovada) AS aprovadas,
                   COALESCE(SUM(ai.valor_proposto) FILTER (WHERE ai.aprovada), 0) AS valor_total
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
                    f"Proposta #{p['id']} — {p['data_avaliacao']} — "
                    f"{p['aprovadas']} peça(s) — R$ {float(p['valor_total']):.2f}"
                )
                opcoes_proposta[rotulo] = p["id"]
            escolha_proposta = st.selectbox(
                "Usar proposta em aberto dessa fornecedora (opcional)",
                options=list(opcoes_proposta.keys()),
            )
            avaliacao_selecionada_id = opcoes_proposta[escolha_proposta]

            if avaliacao_selecionada_id:
                itens_aprovados = run_query(
                    """
                    SELECT ai.descricao, COALESCE(tp.nome, %s) AS tipo_peca, ai.tamanho, ai.valor_proposto
                    FROM avaliacao_item ai
                    LEFT JOIN tipo_peca tp ON tp.id = ai.tipo_peca_id
                    WHERE ai.avaliacao_id = %s AND ai.aprovada = true
                    ORDER BY ai.id
                    """,
                    (nomes_tipo_peca[0], avaliacao_selecionada_id),
                )
                itens_iniciais = pd.DataFrame(
                    [
                        {
                            "descricao": i["descricao"],
                            "tipo_peca": i["tipo_peca"],
                            "tamanho": i["tamanho"] or "",
                            "preco_venda": 0.0,
                            "preco_custo": float(i["valor_proposto"]),
                        }
                        for i in itens_aprovados
                    ]
                )
                st.info(
                    "Peças da proposta carregadas abaixo com o custo já preenchido — "
                    "falta só definir o preço de venda de cada uma."
                )

    data_aceite = st.date_input("Data da compra", value=date.today())

    st.subheader("Peças do lote")
    st.caption(
        "Preencha uma linha por peça, com o custo real pago. Use o + no fim da "
        "tabela pra adicionar linhas."
    )

    if "compra_version" not in st.session_state:
        st.session_state.compra_version = 0

    itens = st.data_editor(
        itens_iniciais,
        num_rows="dynamic",
        use_container_width=True,
        key=f"editor_compra_{st.session_state.compra_version}_{avaliacao_selecionada_id or 'manual'}",
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
        prazo = tipo_selecionado["prazo_dias"]
        if prazo > 0:
            data_vencimento = data_aceite + timedelta(days=prazo)
            status = "pendente"
            data_pagamento = None
        else:
            data_vencimento = None
            status = "pago"
            data_pagamento = data_aceite

        compra = run_query(
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
        )
        compra_id = compra[0]["id"]

        for _, item in itens_validos.iterrows():
            produto = run_query(
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
            )
            produto_id = produto[0]["id"]
            run_query(
                "INSERT INTO movimentacao_estoque (produto_id, tipo, observacao) "
                "VALUES (%s, 'entrada', %s)",
                (produto_id, f"Entrada via compra #{compra_id} ({nome_tipo_compra})"),
                fetch=False,
            )

        if avaliacao_selecionada_id:
            run_query(
                "UPDATE avaliacao SET status = 'aceita', compra_id = %s WHERE id = %s",
                (compra_id, avaliacao_selecionada_id),
                fetch=False,
            )

        st.success(
            f"Compra #{compra_id} registrada com {len(itens_validos)} peça(s), "
            f"total R$ {valor_total:.2f}."
        )
        st.session_state.compra_version += 1
        st.rerun()

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

        pendentes = [c for c in compras if c["status"] == "pendente"]
        if pendentes:
            st.subheader("Marcar compra como paga")
            opcoes_pendentes = {
                f"Compra #{c['id']} — {c['fornecedora']} — R$ {c['valor_total']} "
                f"(vence {c['data_vencimento']})": c["id"]
                for c in pendentes
            }
            escolha = st.selectbox("Selecione a compra", options=list(opcoes_pendentes.keys()))
            if st.button("Marcar como paga"):
                run_query(
                    "UPDATE compra SET status = 'pago', data_pagamento = CURRENT_DATE WHERE id = %s",
                    (opcoes_pendentes[escolha],),
                    fetch=False,
                )
                st.success("Compra marcada como paga.")
                st.rerun()
    else:
        st.info("Nenhuma compra registrada ainda.")

# ================================================================
# Aba: Avaliação de Peças
# ================================================================
with aba_avaliacao:
    st.caption(
        "Registre o lote de peças trazido pela fornecedora pra avaliação, aprove "
        "ou reprove cada peça, e gere o PDF da proposta pra enviar a ela."
    )

    fornecedoras_aval = run_query("SELECT id, nome FROM fornecedora ORDER BY nome")
    if not fornecedoras_aval:
        st.warning("Cadastre uma fornecedora antes de criar uma avaliação.")
    else:
        opcoes_fornecedora_aval = {f["nome"]: f["id"] for f in fornecedoras_aval}
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
                        "valor": 0.0,
                        "observacao": "",
                    }
                ]
            ),
            num_rows="dynamic",
            use_container_width=True,
            key=f"editor_avaliacao_{st.session_state.avaliacao_version}",
            column_config={
                "tipo_peca": st.column_config.SelectboxColumn("Tipo de peça", options=nomes_tipo_peca),
                "aprovada": st.column_config.CheckboxColumn("Aprovada?"),
                "valor": st.column_config.NumberColumn(
                    "Valor proposto (R$)", min_value=0.0, step=1.0, help="Só vale se a peça for aprovada"
                ),
            },
        )

        itens_aval_validos = itens_avaliacao[itens_avaliacao["descricao"].str.strip() != ""]

        if st.button("Salvar avaliação", type="primary", disabled=itens_aval_validos.empty):
            fornecedora_aval_id = opcoes_fornecedora_aval[nome_fornecedora_aval]

            avaliacao = run_query(
                "INSERT INTO avaliacao (fornecedora_id, data_avaliacao) VALUES (%s, %s) RETURNING id",
                (fornecedora_aval_id, data_avaliacao),
            )
            avaliacao_id = avaliacao[0]["id"]

            itens_para_pdf = []
            for _, item in itens_aval_validos.iterrows():
                aprovada = bool(item["aprovada"])
                valor_proposto = float(item["valor"]) if aprovada else None
                run_query(
                    """
                    INSERT INTO avaliacao_item
                        (avaliacao_id, descricao, tipo_peca_id, tamanho, aprovada, valor_proposto, observacao)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        avaliacao_id,
                        item["descricao"],
                        tipo_peca_por_nome.get(item["tipo_peca"]),
                        item["tamanho"] or None,
                        aprovada,
                        valor_proposto,
                        item["observacao"] or None,
                    ),
                    fetch=False,
                )
                itens_para_pdf.append(
                    {
                        "descricao": item["descricao"],
                        "tipo_peca": item["tipo_peca"],
                        "tamanho": item["tamanho"],
                        "aprovada": aprovada,
                        "valor_proposto": valor_proposto,
                        "observacao": item["observacao"],
                    }
                )

            st.success(f"Avaliação #{avaliacao_id} salva com {len(itens_aval_validos)} peça(s).")

            pdf_bytes = gerar_pdf_avaliacao(nome_fornecedora_aval, data_avaliacao, itens_para_pdf)
            st.download_button(
                "Baixar PDF da proposta",
                data=pdf_bytes,
                file_name=f"proposta_avaliacao_{avaliacao_id}.pdf",
                mime="application/pdf",
            )

        st.divider()
        st.subheader("Avaliações recentes")

        avaliacoes = run_query(
            """
            SELECT a.id, f.nome AS fornecedora, a.data_avaliacao, a.status,
                   COUNT(*) FILTER (WHERE ai.aprovada) AS aprovadas,
                   COUNT(*) FILTER (WHERE NOT ai.aprovada) AS reprovadas,
                   COALESCE(SUM(ai.valor_proposto) FILTER (WHERE ai.aprovada), 0) AS valor_total
            FROM avaliacao a
            JOIN fornecedora f ON f.id = a.fornecedora_id
            LEFT JOIN avaliacao_item ai ON ai.avaliacao_id = a.id
            GROUP BY a.id, f.nome, a.data_avaliacao, a.status
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

            col1, col2 = st.columns(2)
            with col1:
                itens_aval_download = run_query(
                    """
                    SELECT ai.descricao, tp.nome AS tipo_peca, ai.tamanho, ai.aprovada,
                           ai.valor_proposto, ai.observacao
                    FROM avaliacao_item ai
                    LEFT JOIN tipo_peca tp ON tp.id = ai.tipo_peca_id
                    WHERE ai.avaliacao_id = %s
                    ORDER BY ai.id
                    """,
                    (aval_selecionada["id"],),
                )
                pdf_bytes_download = gerar_pdf_avaliacao(
                    aval_selecionada["fornecedora"], aval_selecionada["data_avaliacao"], itens_aval_download
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
