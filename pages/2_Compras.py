from datetime import date, timedelta

import pandas as pd
import streamlit as st

from db import run_query, transacao
from branding import aplicar_logo
from auth import exigir_login, botao_logout
from pdf_avaliacao import gerar_pdf_avaliacao
from controle_pagamentos import renderizar_controle_pagamentos
from formatacao import brl, fmt_data, hoje_brasil
from parcelas import dec, editor_parcelas
from compras_parcelas import (
    ParcelasAlteradasError,
    compras_em_aberto,
    parcelas_da_compra,
    reparcelar,
)
from prazos_proposta import (
    PARCELADA_PRIMEIRA_DIAS_UTEIS_PADRAO,
    PARCELADA_QTD_PADRAO,
    PRAZO_A_VISTA_DIAS,
    ROTULO_A_VISTA,
    descricao_parcelada,
    primeiro_vencimento_parcelada,
    rotulo_parcelada,
    vencimento_a_vista,
)
from parcelas import CODIGO_POR_INTERVALO, INTERVALO_POR_CODIGO

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
    limpo["valor_a_vista"] = pd.to_numeric(limpo["valor_a_vista"], errors="coerce").fillna(0.0)
    limpo["valor_parcelado"] = pd.to_numeric(limpo["valor_parcelado"], errors="coerce").fillna(0.0)
    limpo["tipo_peca"] = limpo["tipo_peca"].fillna(tipo_padrao)
    limpo["tamanho"] = limpo["tamanho"].fillna("")
    limpo["marca"] = limpo["marca"].fillna("")
    limpo["observacao"] = limpo["observacao"].fillna("")
    return limpo


def _marca_limpa(valor):
    """Marca digitada (texto livre): tira espaços sobrando; vazio/NaN vira None."""
    if valor is None or (isinstance(valor, float) and valor != valor):
        return None
    return " ".join(str(valor).split()) or None


def _aprovadas_sem_valor(itens_df, config):
    """Peças aprovadas precisam ter valor em cada proposta que vai ser enviada."""
    aprovadas = itens_df[itens_df["aprovada"]]
    faltando = pd.Series(False, index=aprovadas.index)
    if config["envia_a_vista"]:
        faltando |= aprovadas["valor_a_vista"] <= 0
    if config["envia_parcelada"]:
        faltando |= aprovadas["valor_parcelado"] <= 0
    return int(faltando.sum())


def _salvar_itens_avaliacao(executar, avaliacao_id, itens_df, tipo_peca_por_nome, config):
    """Apaga os itens atuais da avaliação e grava os do dataframe editado
    (dentro da transação recebida). O valor de uma proposta que não vai ser
    enviada não é gravado. Retorna a lista pronta pra gerar o PDF."""
    executar("DELETE FROM avaliacao_item WHERE avaliacao_id = %s", (avaliacao_id,))
    itens_para_pdf = []
    for _, item in itens_df.iterrows():
        aprovada = bool(item["aprovada"])
        a_vista = float(item["valor_a_vista"]) if aprovada and config["envia_a_vista"] else None
        parcelado = float(item["valor_parcelado"]) if aprovada and config["envia_parcelada"] else None
        executar(
            """
            INSERT INTO avaliacao_item
                (avaliacao_id, descricao, marca, tipo_peca_id, tamanho, aprovada,
                 valor_a_vista, valor_parcelado, observacao)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                avaliacao_id,
                item["descricao"],
                _marca_limpa(item["marca"]),
                tipo_peca_por_nome.get(item["tipo_peca"]),
                item["tamanho"] or None,
                aprovada,
                a_vista,
                parcelado,
                item["observacao"] or None,
            ),
        )
        itens_para_pdf.append(
            {
                "descricao": item["descricao"],
                "marca": _marca_limpa(item["marca"]),
                "tipo_peca": item["tipo_peca"],
                "tamanho": item["tamanho"],
                "aprovada": aprovada,
                "valor_a_vista": a_vista,
                "valor_parcelado": parcelado,
                "observacao": item["observacao"],
            }
        )
    return itens_para_pdf


def _config_colunas_avaliacao():
    # as duas colunas de valor ficam sempre na tabela: esconder uma delas faria o
    # Streamlit apagar o que já foi digitado. A coluna da proposta que não vai
    # ser enviada é simplesmente ignorada ao salvar.
    return {
        "descricao": st.column_config.TextColumn("Descrição"),
        "marca": st.column_config.TextColumn("Marca"),
        "tipo_peca": st.column_config.SelectboxColumn("Tipo de peça", options=nomes_tipo_peca),
        "tamanho": st.column_config.TextColumn("Tamanho"),
        "aprovada": st.column_config.CheckboxColumn("Aprovada?"),
        "valor_a_vista": st.column_config.NumberColumn(
            "Valor à vista (R$)", min_value=0.0, step=1.0,
            help=f"Valor da peça na proposta à vista (pagamento em até {PRAZO_A_VISTA_DIAS} dias). "
                 "Deixe 0 se não for enviar a proposta à vista.",
        ),
        "valor_parcelado": st.column_config.NumberColumn(
            "Valor parcelado (R$)", min_value=0.0, step=1.0,
            help="Valor total da peça na proposta parcelada (somando todas as parcelas). "
                 "Deixe 0 se não for enviar a proposta parcelada.",
        ),
        "observacao": st.column_config.TextColumn("Observação"),
    }


def _bloco_propostas(prefixo, atual=None):
    """Escolha das propostas que vão para a fornecedora e das condições da
    parcelada. Retorna (config, erro)."""
    atual = atual or {
        "envia_a_vista": True,
        "envia_parcelada": True,
        "parcelada_qtd": PARCELADA_QTD_PADRAO,
        "parcelada_primeira_dias_uteis": PARCELADA_PRIMEIRA_DIAS_UTEIS_PADRAO,
        "parcelada_intervalo": "mensal",
    }
    st.markdown("**Propostas a enviar** — marque uma ou as duas")
    col_av, col_pa = st.columns(2)
    envia_a_vista = col_av.checkbox(
        f"Proposta à vista (pagamento em até {PRAZO_A_VISTA_DIAS} dias)",
        value=atual["envia_a_vista"], key=f"{prefixo}_envia_av",
    )
    envia_parcelada = col_pa.checkbox("Proposta parcelada", value=atual["envia_parcelada"], key=f"{prefixo}_envia_pa")

    config = {
        "envia_a_vista": envia_a_vista,
        "envia_parcelada": envia_parcelada,
        "parcelada_qtd": atual["parcelada_qtd"],
        "parcelada_primeira_dias_uteis": atual["parcelada_primeira_dias_uteis"],
        "parcelada_intervalo": atual["parcelada_intervalo"],
    }
    if envia_parcelada:
        c1, c2, c3 = st.columns(3)
        config["parcelada_qtd"] = int(c1.number_input(
            "Número de parcelas", min_value=1, max_value=24, step=1,
            value=int(atual["parcelada_qtd"]), key=f"{prefixo}_qtd",
        ))
        config["parcelada_primeira_dias_uteis"] = int(c2.number_input(
            "1ª parcela em (dias úteis após o aceite)", min_value=0, max_value=120, step=1,
            value=int(atual["parcelada_primeira_dias_uteis"]), key=f"{prefixo}_dias",
        ))
        rotulos_intervalo = list(INTERVALO_POR_CODIGO.values())
        escolhido = c3.selectbox(
            "Intervalo entre parcelas", rotulos_intervalo,
            index=rotulos_intervalo.index(INTERVALO_POR_CODIGO[atual["parcelada_intervalo"]]),
            key=f"{prefixo}_intervalo", disabled=config["parcelada_qtd"] == 1,
        )
        config["parcelada_intervalo"] = CODIGO_POR_INTERVALO[escolhido]

    erro = None if (envia_a_vista or envia_parcelada) else "Marque ao menos uma proposta para enviar."
    return config, erro


def _gravar_config_avaliacao(executar, avaliacao_id, config):
    executar(
        """
        UPDATE avaliacao
        SET envia_a_vista = %s, envia_parcelada = %s, parcelada_qtd = %s,
            parcelada_primeira_dias_uteis = %s, parcelada_intervalo = %s
        WHERE id = %s
        """,
        (config["envia_a_vista"], config["envia_parcelada"], config["parcelada_qtd"],
         config["parcelada_primeira_dias_uteis"], config["parcelada_intervalo"], avaliacao_id),
    )


def _nomes_propostas(config):
    nomes = []
    if config["envia_a_vista"]:
        nomes.append("à vista")
    if config["envia_parcelada"]:
        nomes.append(rotulo_parcelada(config).lower())
    return " e ".join(nomes)


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
    proposta_escolhida = None  # 'a_vista' ou 'parcelada' (só quando usa uma avaliação)
    config_proposta = None     # condições da avaliação escolhida (parcelas etc.)
    itens_iniciais = pd.DataFrame(
        [
            {
                "descricao": "",
                "marca": "",
                "tipo_peca": nomes_tipo_peca[0],
                "tamanho": "",
                "preco_venda": 0.0,
                "preco_custo": 0.0,
            }
        ]
    )

    if fornecedora_id:
        propostas_abertas = run_query(
            """
            SELECT a.id, a.data_avaliacao,
                   a.envia_a_vista, a.envia_parcelada, a.parcelada_qtd,
                   a.parcelada_primeira_dias_uteis, a.parcelada_intervalo,
                   COUNT(*) FILTER (WHERE ai.aprovada) AS aprovadas,
                   COALESCE(SUM(ai.valor_a_vista) FILTER (WHERE ai.aprovada), 0) AS total_a_vista,
                   COALESCE(SUM(ai.valor_parcelado) FILTER (WHERE ai.aprovada), 0) AS total_parcelado
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
            aval_por_id = {p["id"]: p for p in propostas_abertas}
            for p in propostas_abertas:
                totais = []
                if p["envia_a_vista"]:
                    totais.append(f"à vista: {brl(p['total_a_vista'])}")
                if p["envia_parcelada"]:
                    totais.append(f"{rotulo_parcelada(p).lower()}: {brl(p['total_parcelado'])}")
                rotulo = (
                    f"Avaliação #{p['id']} — {fmt_data(p['data_avaliacao'])} — "
                    f"{p['aprovadas']} peça(s) — " + " · ".join(totais)
                )
                opcoes_proposta[rotulo] = p["id"]
            escolha_proposta = st.selectbox(
                "Usar avaliação em aberto dessa fornecedora (opcional)",
                options=list(opcoes_proposta.keys()),
            )
            avaliacao_selecionada_id = opcoes_proposta[escolha_proposta]

            if avaliacao_selecionada_id:
                config_proposta = aval_por_id[avaliacao_selecionada_id]
                opcoes_prazo = {}
                if config_proposta["envia_a_vista"]:
                    opcoes_prazo[ROTULO_A_VISTA] = "a_vista"
                if config_proposta["envia_parcelada"]:
                    opcoes_prazo[
                        f"{rotulo_parcelada(config_proposta)} — "
                        f"{descricao_parcelada(config_proposta['total_parcelado'], config_proposta)}"
                    ] = "parcelada"
                escolha_prazo = st.radio(
                    "Proposta aceita pela fornecedora",
                    options=list(opcoes_prazo.keys()),
                    key=f"proposta_aceita_{avaliacao_selecionada_id}",
                    help="Define o custo das peças e as parcelas sugeridas para o pagamento.",
                )
                proposta_escolhida = opcoes_prazo[escolha_prazo]

                itens_aprovados = run_query(
                    """
                    SELECT ai.descricao, ai.marca, COALESCE(tp.nome, %s) AS tipo_peca, ai.tamanho,
                           ai.valor_a_vista, ai.valor_parcelado
                    FROM avaliacao_item ai
                    LEFT JOIN tipo_peca tp ON tp.id = ai.tipo_peca_id
                    WHERE ai.avaliacao_id = %s AND ai.aprovada = true
                    ORDER BY ai.id
                    """,
                    (nomes_tipo_peca[0], avaliacao_selecionada_id),
                )
                coluna_valor = "valor_a_vista" if proposta_escolhida == "a_vista" else "valor_parcelado"
                itens_iniciais = pd.DataFrame(
                    [
                        {
                            "descricao": i["descricao"],
                            "marca": i["marca"] or "",
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

    data_aceite = st.date_input("Data da compra", value=hoje_brasil(), format="DD/MM/YYYY")

    # vencimento sugerido pela proposta ou pelo tipo de compra (vira o 1º vencimento)
    if proposta_escolhida == "a_vista":
        vencimento_sugerido = vencimento_a_vista(data_aceite)
    elif proposta_escolhida == "parcelada":
        vencimento_sugerido = primeiro_vencimento_parcelada(
            data_aceite, config_proposta["parcelada_primeira_dias_uteis"]
        )
    elif tipo_selecionado["prazo_dias"] > 0:
        vencimento_sugerido = data_aceite + timedelta(days=tipo_selecionado["prazo_dias"])
    else:
        vencimento_sugerido = None

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
            "marca": st.column_config.TextColumn("Marca"),
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

    # ---- pagamento: à vista ou a prazo/parcelado ----
    st.subheader("Pagamento")
    A_VISTA = "À vista (já pago na data da compra)"
    A_PRAZO = "A prazo / parcelado"
    parcelas_compra, erro_parcelas = [], None
    if itens_validos.empty:
        st.caption("Preencha as peças do lote para definir o pagamento.")
        modo_pagamento = A_VISTA
    elif valor_total <= 0:
        st.caption("Lote sem custo: a compra entra como paga.")
        modo_pagamento = A_VISTA
    else:
        modo_pagamento = st.radio(
            "Como vai ser pago",
            [A_VISTA, A_PRAZO],
            index=1 if vencimento_sugerido else 0,
            horizontal=True,
            key=f"compra_modo_{versao_compra}_{tipo_selecionado['id']}_{proposta_escolhida or ''}",
            help="Qualquer tipo de compra pode ser parcelado. As parcelas são pagas "
                 "na aba Controle de pagamentos (peças).",
        )
        if modo_pagamento == A_PRAZO:
            if proposta_escolhida:
                st.caption("Parcelas sugeridas pela proposta aceita; dá para ajustar antes de registrar.")
            parcelada = proposta_escolhida == "parcelada"
            parcelas_compra, erro_parcelas = editor_parcelas(
                valor_total,
                data_aceite,
                chave=f"compra_parc_{versao_compra}_{tipo_selecionado['id']}_{avaliacao_selecionada_id or ''}_{proposta_escolhida or ''}",
                quantidade_padrao=config_proposta["parcelada_qtd"] if parcelada else 1,
                vencimento_padrao=vencimento_sugerido or data_aceite + timedelta(days=30),
                intervalo_padrao=(
                    INTERVALO_POR_CODIGO[config_proposta["parcelada_intervalo"]] if parcelada
                    else INTERVALO_POR_CODIGO["mensal"]
                ),
            )
            if erro_parcelas:
                st.error(erro_parcelas)

    a_prazo = modo_pagamento == A_PRAZO
    if st.button(
        "Registrar compra", type="primary",
        disabled=itens_validos.empty or (a_prazo and bool(erro_parcelas)),
    ):
        if a_prazo:
            status = "pendente"
            data_vencimento = parcelas_compra[0]["data_vencimento"]
            data_pagamento = None
        else:
            status = "pago"
            data_vencimento = data_aceite
            data_pagamento = data_aceite
            parcelas_compra = [{"numero": 1, "data_vencimento": data_aceite, "valor": valor_total}]

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

                for parcela in parcelas_compra:
                    executar(
                        """
                        INSERT INTO compra_parcela
                            (compra_id, numero, valor, data_vencimento, status, data_pagamento)
                        VALUES (%s, %s, %s, %s, %s, %s)
                        """,
                        (compra_id, parcela["numero"], parcela["valor"], parcela["data_vencimento"],
                         status, data_pagamento),
                    )

                for _, item in itens_validos.iterrows():
                    produto = executar(
                        """
                        INSERT INTO produto (compra_id, descricao, marca, tipo_peca_id, tamanho, preco_custo, preco_venda)
                        VALUES (%s, %s, %s, %s, %s, %s, %s)
                        RETURNING id
                        """,
                        (
                            compra_id,
                            item["descricao"],
                            _marca_limpa(item["marca"]),
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
            if not a_prazo:
                pagamento_txt = ", paga à vista."
            elif len(parcelas_compra) == 1:
                pagamento_txt = f", vence em {fmt_data(data_vencimento)}."
            else:
                pagamento_txt = (
                    f", em {len(parcelas_compra)} parcelas (1ª vence em {fmt_data(data_vencimento)})."
                )
            st.session_state["compra_sucesso"] = (
                f"Compra #{compra_id} registrada com {len(itens_validos)} peça(s), "
                f"total {brl(valor_total)}" + pagamento_txt
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
               c.data_aceite, c.valor_total, c.data_vencimento, c.status, c.data_pagamento,
               COUNT(p.id) AS parcelas,
               COUNT(p.id) FILTER (WHERE p.status = 'pago') AS pagas,
               COALESCE(SUM(p.valor) FILTER (WHERE p.status = 'pendente'), 0) AS em_aberto
        FROM compra c
        JOIN tipo_compra tc ON tc.id = c.tipo_compra_id
        LEFT JOIN fornecedora f ON f.id = c.fornecedora_id
        LEFT JOIN compra_parcela p ON p.compra_id = c.id
        GROUP BY c.id, tc.nome, f.nome
        ORDER BY c.data_aceite DESC, c.id DESC
        LIMIT 30
        """
    )

    if compras:
        st.dataframe(
            [
                {
                    "Compra": f"#{c['id']}",
                    "Tipo de compra": c["tipo_compra"],
                    "Fornecedora": c["fornecedora"],
                    "Data da compra": fmt_data(c["data_aceite"]),
                    "Valor total": brl(c["valor_total"]),
                    "Parcelas pagas": f"{c['pagas']}/{c['parcelas']}",
                    "Em aberto": brl(c["em_aberto"]) if c["em_aberto"] else "—",
                    "Próximo vencimento": fmt_data(c["data_vencimento"]) if c["status"] == "pendente" else "—",
                    "Situação": "Paga" if c["status"] == "pago" else "Em aberto",
                    "Quitada em": fmt_data(c["data_pagamento"]) if c["status"] == "pago" else "—",
                }
                for c in compras
            ],
            use_container_width=True,
            hide_index=True,
        )
        st.caption("Pra pagar uma parcela em aberto, use a aba Controle de pagamentos (peças).")
    else:
        st.info("Nenhuma compra registrada ainda.")

    # ---- parcelas e vencimentos de compras em aberto ----
    em_aberto = compras_em_aberto()
    if em_aberto:
        st.subheader("Parcelas e vencimentos")
        st.caption(
            "Para compras em aberto: mude vencimentos, valores ou o número de parcelas. "
            "Parcelas já pagas não mudam."
        )

        mensagem_parc = st.session_state.pop("reparc_sucesso", None)
        if mensagem_parc:
            st.success(mensagem_parc)

        opcoes_reparc = {
            f"Compra #{c['id']} — {c['fornecedora']} — {brl(c['valor_total'])} — "
            f"{c['pagas']}/{c['parcelas']} parcela(s) paga(s) — próximo venc. {fmt_data(c['data_vencimento'])}": c
            for c in em_aberto
        }
        escolha_reparc = st.selectbox("Compra", options=list(opcoes_reparc), key="reparc_sel")
        compra_reparc = opcoes_reparc[escolha_reparc]
        parcelas_atuais = parcelas_da_compra(compra_reparc["id"])
        pagas = [p for p in parcelas_atuais if p["status"] == "pago"]
        pendentes = [p for p in parcelas_atuais if p["status"] == "pendente"]

        st.dataframe(
            [
                {
                    "Parcela": f"{p['numero']}/{len(parcelas_atuais)}",
                    "Vencimento": fmt_data(p["data_vencimento"]),
                    "Valor": brl(p["valor"]),
                    "Situação": "Paga" if p["status"] == "pago" else "Em aberto",
                    "Paga em": fmt_data(p["data_pagamento"]) if p["status"] == "pago" else "—",
                }
                for p in parcelas_atuais
            ],
            use_container_width=True,
            hide_index=True,
        )

        restante = sum((dec(p["valor"]) for p in pendentes), dec(0))
        st.markdown(f"**Em aberto: {brl(restante)}** em {len(pendentes)} parcela(s).")
        versao_reparc = st.session_state.setdefault("reparc_versao", 0)
        data_minima = min([compra_reparc["data_aceite"]] + [p["data_vencimento"] for p in pendentes])
        novas_parcelas, erro_reparc = editor_parcelas(
            restante,
            data_minima,
            chave=f"reparc_{compra_reparc['id']}_{versao_reparc}",
            quantidade_padrao=len(pendentes),
            vencimento_padrao=pendentes[0]["data_vencimento"],
            iniciais=pendentes,
            rotulo_quantidade="Parcelas em aberto",
        )
        if erro_reparc:
            st.error(erro_reparc)

        sem_mudanca = not erro_reparc and [
            (p["data_vencimento"], dec(p["valor"])) for p in novas_parcelas
        ] == [(p["data_vencimento"], dec(p["valor"])) for p in sorted(pendentes, key=lambda x: (x["data_vencimento"], x["numero"]))]
        if st.button(
            "Salvar parcelas", key="reparc_salvar", type="primary",
            disabled=bool(erro_reparc) or sem_mudanca,
        ):
            try:
                reparcelar(compra_reparc["id"], [p["id"] for p in pendentes], novas_parcelas)
            except ParcelasAlteradasError as e:
                st.error(str(e))
            except Exception as e:
                st.error(f"Não foi possível salvar. Nada foi gravado. Erro: {e}")
            else:
                st.session_state["reparc_sucesso"] = (
                    f"Parcelas da compra #{compra_reparc['id']} atualizadas: "
                    f"{len(novas_parcelas)} parcela(s) em aberto, 1ª vence em "
                    f"{fmt_data(novas_parcelas[0]['data_vencimento'])}."
                )
                st.session_state["reparc_versao"] = versao_reparc + 1
                st.rerun()

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
                SELECT a.id, a.fornecedora_id, f.nome AS fornecedora, a.data_avaliacao, a.status,
                       a.envia_a_vista, a.envia_parcelada, a.parcelada_qtd,
                       a.parcelada_primeira_dias_uteis, a.parcelada_intervalo
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
                SELECT ai.descricao, ai.marca, COALESCE(tp.nome, %s) AS tipo_peca, ai.tamanho,
                       ai.aprovada, ai.valor_a_vista, ai.valor_parcelado, ai.observacao
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
                        "marca": i["marca"] or "",
                        "tipo_peca": i["tipo_peca"],
                        "tamanho": i["tamanho"] or "",
                        "aprovada": i["aprovada"],
                        "valor_a_vista": float(i["valor_a_vista"]) if i["valor_a_vista"] is not None else 0.0,
                        "valor_parcelado": float(i["valor_parcelado"]) if i["valor_parcelado"] is not None else 0.0,
                        "observacao": i["observacao"] or "",
                    }
                    for i in itens_atuais
                ]
            )

            config_edicao, erro_config_edicao = _bloco_propostas(f"prop_edicao_{aval_id_edicao}", aval_atual)
            if erro_config_edicao:
                st.error(erro_config_edicao)

            itens_editados = st.data_editor(
                df_edicao,
                num_rows="dynamic",
                use_container_width=True,
                key=f"editor_edicao_{aval_id_edicao}",
                column_config=_config_colunas_avaliacao(),
            )
            itens_editados_limpos = _limpar_itens_avaliacao(itens_editados, nomes_tipo_peca[0])
            sem_valor_edicao = _aprovadas_sem_valor(itens_editados_limpos, config_edicao)
            if sem_valor_edicao and not erro_config_edicao:
                st.warning(
                    f"{sem_valor_edicao} peça(s) aprovada(s) sem valor na proposta {_nomes_propostas(config_edicao)} — "
                    "preencha para salvar."
                )

            col_salvar, col_cancelar = st.columns(2)
            if col_salvar.button(
                "Salvar alterações",
                type="primary",
                disabled=itens_editados_limpos.empty or bool(sem_valor_edicao) or bool(erro_config_edicao),
            ):
                with transacao() as executar:
                    executar(
                        "UPDATE avaliacao SET fornecedora_id = %s, data_avaliacao = %s WHERE id = %s",
                        (opcoes_fornecedora_aval[nome_fornecedora_edicao], data_avaliacao_edicao, aval_id_edicao),
                    )
                    _gravar_config_avaliacao(executar, aval_id_edicao, config_edicao)
                    itens_para_pdf = _salvar_itens_avaliacao(
                        executar, aval_id_edicao, itens_editados_limpos, tipo_peca_por_nome, config_edicao
                    )
                st.success(f"Avaliação #{aval_id_edicao} atualizada.")
                pdf_bytes = gerar_pdf_avaliacao(
                    nome_fornecedora_edicao, data_avaliacao_edicao, itens_para_pdf, config_edicao
                )
                st.download_button(
                    f"Baixar PDF atualizado (proposta {_nomes_propostas(config_edicao)})",
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
            data_avaliacao = st.date_input("Data da avaliação", value=hoje_brasil(), key="data_aval")

            if "avaliacao_version" not in st.session_state:
                st.session_state.avaliacao_version = 0

            config_nova, erro_config_nova = _bloco_propostas(f"prop_nova_{st.session_state.avaliacao_version}")
            if erro_config_nova:
                st.error(erro_config_nova)

            itens_avaliacao = st.data_editor(
                pd.DataFrame(
                    [
                        {
                            "descricao": "",
                            "marca": "",
                            "tipo_peca": nomes_tipo_peca[0],
                            "tamanho": "",
                            "aprovada": False,
                            "valor_a_vista": 0.0,
                            "valor_parcelado": 0.0,
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
                "Preencha o valor de cada peça aprovada nas propostas marcadas acima (a coluna da "
                "proposta que não vai ser enviada é ignorada). Se forem as duas, o PDF leva as duas "
                "e só uma será fechada."
            )

            itens_aval_validos = _limpar_itens_avaliacao(itens_avaliacao, nomes_tipo_peca[0])
            sem_valor = _aprovadas_sem_valor(itens_aval_validos, config_nova)
            if sem_valor and not erro_config_nova:
                st.warning(
                    f"{sem_valor} peça(s) aprovada(s) sem valor na proposta {_nomes_propostas(config_nova)} — "
                    "preencha para salvar."
                )

            if st.button(
                "Salvar avaliação", type="primary",
                disabled=itens_aval_validos.empty or bool(sem_valor) or bool(erro_config_nova),
            ):
                fornecedora_aval_id = opcoes_fornecedora_aval[nome_fornecedora_aval]

                with transacao() as executar:
                    avaliacao = executar(
                        "INSERT INTO avaliacao (fornecedora_id, data_avaliacao) VALUES (%s, %s) RETURNING id",
                        (fornecedora_aval_id, data_avaliacao),
                        fetch=True,
                    )
                    avaliacao_id = avaliacao[0]["id"]
                    _gravar_config_avaliacao(executar, avaliacao_id, config_nova)
                    itens_para_pdf = _salvar_itens_avaliacao(
                        executar, avaliacao_id, itens_aval_validos, tipo_peca_por_nome, config_nova
                    )

                st.success(f"Avaliação #{avaliacao_id} salva com {len(itens_aval_validos)} peça(s).")

                pdf_bytes = gerar_pdf_avaliacao(nome_fornecedora_aval, data_avaliacao, itens_para_pdf, config_nova)
                st.download_button(
                    f"Baixar PDF (proposta {_nomes_propostas(config_nova)})",
                    data=pdf_bytes,
                    file_name=f"proposta_avaliacao_{avaliacao_id}.pdf",
                    mime="application/pdf",
                )

        st.divider()
        st.subheader("Avaliações recentes")

        avaliacoes = run_query(
            """
            SELECT a.id, f.nome AS fornecedora, a.data_avaliacao, a.status, a.proposta_aceita,
                   a.envia_a_vista, a.envia_parcelada, a.parcelada_qtd,
                   a.parcelada_primeira_dias_uteis, a.parcelada_intervalo,
                   COUNT(ai.id) FILTER (WHERE ai.aprovada) AS aprovadas,
                   COUNT(ai.id) FILTER (WHERE NOT ai.aprovada) AS reprovadas,
                   COALESCE(SUM(ai.valor_a_vista) FILTER (WHERE ai.aprovada), 0) AS total_a_vista,
                   COALESCE(SUM(ai.valor_parcelado) FILTER (WHERE ai.aprovada), 0) AS total_parcelado
            FROM avaliacao a
            JOIN fornecedora f ON f.id = a.fornecedora_id
            LEFT JOIN avaliacao_item ai ON ai.avaliacao_id = a.id
            GROUP BY a.id, f.nome
            ORDER BY a.data_avaliacao DESC, a.id DESC
            LIMIT 30
            """
        )

        if avaliacoes:
            nomes_aceita = {"a_vista": "À vista", "parcelada": "Parcelada"}
            st.dataframe(
                [
                    {
                        "Avaliação": f"#{a['id']}",
                        "Fornecedora": a["fornecedora"],
                        "Data": fmt_data(a["data_avaliacao"]),
                        "Situação": a["status"],
                        "Propostas enviadas": _nomes_propostas(a).capitalize(),
                        "Aprovadas": a["aprovadas"],
                        "Reprovadas": a["reprovadas"],
                        "Total à vista": brl(a["total_a_vista"]) if a["envia_a_vista"] else "—",
                        "Total parcelado": brl(a["total_parcelado"]) if a["envia_parcelada"] else "—",
                        "Proposta aceita": nomes_aceita.get(a["proposta_aceita"], "—"),
                    }
                    for a in avaliacoes
                ],
                use_container_width=True,
                hide_index=True,
            )

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
                    SELECT ai.descricao, ai.marca, tp.nome AS tipo_peca, ai.tamanho, ai.aprovada,
                           ai.valor_a_vista, ai.valor_parcelado, ai.observacao
                    FROM avaliacao_item ai
                    LEFT JOIN tipo_peca tp ON tp.id = ai.tipo_peca_id
                    WHERE ai.avaliacao_id = %s
                    ORDER BY ai.id
                    """,
                    (aval_selecionada["id"],),
                )
                pdf_bytes_download = gerar_pdf_avaliacao(
                    aval_selecionada["fornecedora"],
                    aval_selecionada["data_avaliacao"],
                    itens_aval_download,
                    aval_selecionada,
                    aval_selecionada["proposta_aceita"],
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
