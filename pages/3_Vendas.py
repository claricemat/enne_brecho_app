import re
from datetime import datetime, timedelta, timezone

import pandas as pd
import streamlit as st

from db import run_query, transacao
from branding import aplicar_logo
from auth import exigir_login, botao_logout
from controle_vendas import (
    FORMA_FIADO,
    FORMAS_RECEBIMENTO,
    RecebimentoInvalidoError,
    chave_cliente,
    clientes_fiel,
    desfazer_entrega,
    distribuir_pagamento,
    excluir_recebimento,
    marcar_entregues,
    recebido_no_periodo,
    recebimentos_recentes,
    registrar_recebimento,
    resumo_por_cliente,
    vendas_fiado_em_aberto,
    vendas_online,
)
from devolucoes import (
    FORMAS_REEMBOLSO,
    DevolucaoInvalidaError,
    cancelar_devolucao,
    devolucoes_recentes,
    itens_vendidos,
    ja_devolvido_por_venda,
    registrar_devolucao,
    valores_sugeridos,
)
from formatacao import brl, fmt_data, hoje_brasil
from parcelas import dec

st.set_page_config(page_title="Vendas", page_icon="assets/icone_coracao.png", layout="wide")
aplicar_logo()
exigir_login()
botao_logout()
st.title("Vendas")

if "venda_version" not in st.session_state:
    st.session_state.venda_version = 0

mensagem_venda = st.session_state.pop("venda_sucesso", None)

aba_venda, aba_fiado, aba_entregas, aba_devolucao = st.tabs(
    ["Registrar venda", "Cliente fiel (fiado)", "Entregas online", "Devolução de peças"]
)


def _aba_registrar_venda():
    produtos = run_query(
        """
        SELECT p.id, p.descricao, p.marca, tp.nome AS tipo_peca, p.tamanho, p.preco_venda
        FROM produto p
        LEFT JOIN tipo_peca tp ON tp.id = p.tipo_peca_id
        WHERE p.status = 'em_estoque'
        ORDER BY p.descricao
        """
    )

    if not produtos:
        st.info("Não há peças em estoque no momento.")
        return

    # ---- sacola da venda: peças marcadas, guardadas entre uma pesquisa e outra ----
    # {código da peça: preço vendido}. Pesquisar outra coisa não tira nada daqui.
    sacola = st.session_state.setdefault("venda_sacola", {})
    versao_sacola = st.session_state.setdefault("venda_sacola_versao", 0)
    produto_por_id = {p["id"]: p for p in produtos}

    # peça que saiu do estoque enquanto estava na sacola (ex.: a outra pessoa vendeu)
    fora_do_estoque = [pid for pid in sacola if pid not in produto_por_id]
    if fora_do_estoque:
        for pid in fora_do_estoque:
            sacola.pop(pid)
        st.warning(
            "Saíram da sacola por não estarem mais no estoque: "
            + ", ".join(f"#{pid}" for pid in fora_do_estoque) + "."
        )

    def _preco(valor, padrao):
        valor = pd.to_numeric(valor, errors="coerce")
        return float(padrao if pd.isna(valor) else valor)

    df_estoque = pd.DataFrame(produtos)
    # o banco devolve Decimal; a tabela e o desconto trabalham com número comum (float)
    df_estoque["preco_venda"] = pd.to_numeric(df_estoque["preco_venda"], errors="coerce").fillna(0.0).astype(float)
    df_estoque.insert(0, "vender", df_estoque["id"].isin(sacola))
    df_estoque["preco_vendido"] = [
        sacola.get(pid, preco) for pid, preco in zip(df_estoque["id"], df_estoque["preco_venda"])
    ]

    busca = st.text_input(
        "Buscar peça (por código/ID, descrição ou marca)",
        placeholder="ex: 42, camisa azul, Farm",
        help="O código é o número que está na etiqueta. Dá para digitar vários de uma vez: 12, 15, 20. "
             "As peças marcadas continuam na sacola quando você pesquisa outra coisa.",
        key=f"venda_busca_{st.session_state.venda_version}",
    )
    # cada pesquisa nova abre a tabela "limpa", preenchida a partir da sacola
    if st.session_state.get("venda_ultima_busca") != busca:
        st.session_state["venda_ultima_busca"] = busca
        st.session_state["venda_busca_n"] = st.session_state.get("venda_busca_n", 0) + 1

    if busca:
        termo = busca.strip()
        mascara = (
            df_estoque["descricao"].str.contains(termo, case=False, na=False, regex=False)
            | df_estoque["marca"].str.contains(termo, case=False, na=False, regex=False)
        )
        # números (com ou sem #, separados por vírgula/espaço) filtram pelo código da peça
        codigos = [t.lstrip("#") for t in re.split(r"[\s,;]+", termo) if t]
        if codigos and all(c.isdigit() for c in codigos):
            mascara |= df_estoque["id"].isin({int(c) for c in codigos})
        df_estoque = df_estoque[mascara].reset_index(drop=True)

    st.caption(
        "Marque as peças vendidas: elas vão para a sacola da venda (logo abaixo) e continuam lá "
        "quando você pesquisa outra peça. O preço vem pré-preenchido com o preço de venda, "
        "mas pode ser ajustado (ex: negociação item a item)."
    )

    if df_estoque.empty:
        st.warning("Nenhuma peça em estoque bate com essa busca.")
    else:
        editado = st.data_editor(
            df_estoque,
            use_container_width=True,
            hide_index=True,
            disabled=["id", "descricao", "marca", "tipo_peca", "tamanho", "preco_venda"],
            key=(
                f"editor_venda_{st.session_state.venda_version}_{versao_sacola}_"
                f"{st.session_state['venda_busca_n']}"
            ),
            column_config={
                "vender": st.column_config.CheckboxColumn("Vender?"),
                "marca": st.column_config.TextColumn("Marca"),
                "preco_vendido": st.column_config.NumberColumn(
                    "Preço vendido (R$)", min_value=0.0, step=1.0
                ),
            },
        )
        # atualiza a sacola só com as peças que estão na tela; as outras ficam como estão
        for _, linha in editado.iterrows():
            pid = int(linha["id"])
            if bool(linha["vender"]):
                sacola[pid] = _preco(linha["preco_vendido"], linha["preco_venda"])
            else:
                sacola.pop(pid, None)

    # ---- a sacola ----
    if sacola:
        st.subheader(f"Sacola desta venda — {len(sacola)} peça(s)")
        conteudo = tuple(sorted(sacola.items()))
        tabela_sacola = st.data_editor(
            pd.DataFrame(
                [
                    {
                        "manter": True,
                        "id": pid,
                        "descricao": produto_por_id[pid]["descricao"],
                        "marca": produto_por_id[pid]["marca"] or "",
                        "tamanho": produto_por_id[pid]["tamanho"] or "",
                        "preco_vendido": preco,
                    }
                    for pid, preco in sacola.items()
                ]
            ),
            use_container_width=True,
            hide_index=True,
            num_rows="fixed",
            disabled=["id", "descricao", "marca", "tamanho"],
            key=f"sacola_{st.session_state.venda_version}_{hash(conteudo)}",
            column_config={
                "manter": st.column_config.CheckboxColumn("Na sacola?", help="Desmarque para tirar a peça da venda."),
                "id": st.column_config.NumberColumn("Código", format="%d"),
                "descricao": st.column_config.TextColumn("Descrição"),
                "marca": st.column_config.TextColumn("Marca"),
                "tamanho": st.column_config.TextColumn("Tamanho"),
                "preco_vendido": st.column_config.NumberColumn("Preço vendido (R$)", min_value=0.0, step=1.0),
            },
        )
        nova_sacola = {
            int(l["id"]): _preco(l["preco_vendido"], produto_por_id[int(l["id"])]["preco_venda"] or 0)
            for _, l in tabela_sacola.iterrows()
            if bool(l["manter"])
        }
        if st.button("Esvaziar sacola", key=f"venda_esvaziar_{st.session_state.venda_version}"):
            nova_sacola = {}
        if nova_sacola != sacola:
            # mudou pela sacola: refaz a tela para a tabela de cima mostrar o mesmo
            st.session_state["venda_sacola"] = nova_sacola
            st.session_state["venda_sacola_versao"] = versao_sacola + 1
            st.rerun()

    selecionados = pd.DataFrame(
        [{"id": pid, "preco_vendido": preco} for pid, preco in sacola.items()],
        columns=["id", "preco_vendido"],
    )

    v = st.session_state.venda_version
    hoje = hoje_brasil()
    col_data, _ = st.columns(2)
    data_venda = col_data.date_input(
        "Data da venda", value=hoje, max_value=hoje, format="DD/MM/YYYY", key=f"venda_data_{v}",
        help="Já vem com a data de hoje. Para lançar uma venda de outro dia, escolha uma data anterior.",
    )
    venda_retroativa = data_venda < hoje
    if venda_retroativa:
        col_data.caption(f"Venda de um dia anterior: vai ser registrada em {fmt_data(data_venda)}.")

    col1, col2 = st.columns(2)
    forma_pagamento = col1.selectbox(
        "Forma de pagamento",
        ["Dinheiro", "Pix", "Cartão de Crédito", "Cartão de Débito", "Crédito Loja", FORMA_FIADO, "Outro"],
        key=f"venda_forma_{v}",
        help=f"\"{FORMA_FIADO}\" é a venda fiado: a cliente paga depois. "
             "Acompanhe e registre os pagamentos na aba Cliente fiel (fiado).",
    )
    canal_rotulo = col2.radio("Onde foi a venda", ["Na loja", "Online"], horizontal=True, key=f"venda_canal_{v}")
    canal = "online" if canal_rotulo == "Online" else "loja"
    entregue = None
    if canal == "online":
        entregue = col2.checkbox(
            "Já foi entregue", value=False, key=f"venda_entregue_{v}",
            help="Se ainda não foi, a venda fica na aba Entregas online até ser marcada como entregue.",
        )

    fiado = forma_pagamento == FORMA_FIADO
    if fiado:
        NOVA_CLIENTE = "+ Nova cliente…"
        conhecidas = clientes_fiel()
        escolha_cliente = st.selectbox(
            "Cliente (obrigatório no fiado)", conhecidas + [NOVA_CLIENTE],
            index=None if conhecidas else 0, placeholder="Escolha a cliente",
            key=f"venda_cliente_fiel_{v}",
        )
        if escolha_cliente == NOVA_CLIENTE:
            cliente = st.text_input("Nome da nova cliente", key=f"venda_cliente_nova_{v}")
        else:
            cliente = escolha_cliente or ""
    else:
        cliente = st.text_input("Cliente (opcional)", key=f"venda_cliente_{v}")
    cliente = " ".join(str(cliente or "").split())

    tipos_receita = run_query("SELECT id, nome FROM plano_contas WHERE tipo = 'receita' ORDER BY nome")
    opcoes_receita = {r["nome"]: r["id"] for r in tipos_receita} if tipos_receita else {}
    nomes_receita = list(opcoes_receita.keys())
    indice_padrao = nomes_receita.index("Venda de peças") if "Venda de peças" in nomes_receita else 0

    col3, col4 = st.columns(2)
    desconto = col3.number_input("Desconto (R$)", min_value=0.0, step=1.0, value=0.0)
    if nomes_receita:
        nome_receita = col4.selectbox("Tipo de receita", options=nomes_receita, index=indice_padrao)
        plano_conta_id = opcoes_receita[nome_receita]
    else:
        plano_conta_id = None

    if not selecionados.empty:
        subtotal = selecionados["preco_vendido"].sum()
        valor_total_preview = max(subtotal - desconto, 0)
        st.metric("Total da venda", brl(valor_total_preview), f"desconto {brl(desconto)}")

    if fiado and not cliente:
        st.info("Informe a cliente para registrar uma venda em Cliente fiel.")
    if st.button("Registrar venda", type="primary", disabled=selecionados.empty or (fiado and not cliente)):
        subtotal = round(selecionados["preco_vendido"].sum(), 2)
        valor_total = round(max(subtotal - desconto, 0), 2)
        if venda_retroativa:
            # meio-dia no horário de Brasília: a venda nunca "escorrega" para outro dia
            momento_venda = datetime(data_venda.year, data_venda.month, data_venda.day, 12, 0,
                                     tzinfo=timezone(timedelta(hours=-3)))
        else:
            momento_venda = datetime.now(timezone.utc)

        try:
            # tudo numa transação: ou a venda inteira é gravada, ou nada
            with transacao() as executar:
                venda_id = executar(
                    "INSERT INTO venda (data_venda, forma_pagamento, valor_total, cliente, desconto, "
                    "plano_conta_id, canal, entregue, data_entrega) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
                    (momento_venda, forma_pagamento, valor_total, cliente or None, desconto,
                     plano_conta_id, canal, entregue, data_venda if entregue else None),
                    fetch=True,
                )[0]["id"]

                for _, item in selecionados.iterrows():
                    produto_id = int(item["id"])
                    # só vende se a peça ainda estiver no estoque (a outra pessoa pode ter vendido)
                    ainda_em_estoque = executar(
                        "UPDATE produto SET status = 'vendido' WHERE id = %s AND status = 'em_estoque' RETURNING id",
                        (produto_id,),
                        fetch=True,
                    )
                    if not ainda_em_estoque:
                        raise ValueError(
                            f"A peça #{produto_id} não está mais no estoque (talvez tenha sido vendida "
                            "pela outra pessoa). Nada foi gravado — atualize a página."
                        )
                    executar(
                        "INSERT INTO item_venda (venda_id, produto_id, preco_vendido) VALUES (%s, %s, %s)",
                        (venda_id, produto_id, item["preco_vendido"]),
                    )
                    executar(
                        "INSERT INTO movimentacao_estoque (produto_id, tipo, observacao) "
                        "VALUES (%s, 'saida', %s)",
                        (produto_id, f"Saída via venda #{venda_id}"),
                    )
        except ValueError as e:
            st.error(str(e))
            return
        except Exception as e:
            st.error(f"Não foi possível registrar a venda. Nada foi gravado. Erro: {e}")
            return

        extras = []
        if canal == "online":
            extras.append("online, já entregue" if entregue else "online, aguardando entrega")
        if fiado:
            extras.append(f"fiado para {cliente}")
        st.session_state["venda_sucesso"] = (
            f"Venda #{venda_id} registrada"
            + (f" com data de {fmt_data(data_venda)}" if venda_retroativa else "")
            + f" — {len(selecionados)} peça(s), "
            f"total {brl(valor_total)} (desconto de {brl(desconto)})"
            + (f" — {'; '.join(extras)}." if extras else ".")
        )
        st.session_state.venda_version += 1
        st.session_state["venda_sacola"] = {}
        st.rerun()


def _vendas_recentes():
    st.divider()
    st.subheader("Vendas recentes")
    vendas = run_query(
        """
        SELECT v.id, v.data_venda, v.cliente, v.forma_pagamento, v.desconto, v.valor_total,
               v.canal, v.entregue,
               COALESCE((SELECT SUM(d.valor_devolvido) FROM devolucao d WHERE d.venda_id = v.id), 0) AS devolvido,
               pc.nome AS tipo_receita
        FROM venda v
        LEFT JOIN plano_contas pc ON pc.id = v.plano_conta_id
        ORDER BY v.data_venda DESC, v.id DESC LIMIT 30
        """
    )
    if vendas:
        st.dataframe(
            [
                {
                    "Venda": f"#{v['id']}",
                    "Data": fmt_data(v["data_venda"]),
                    "Cliente": v["cliente"] or "",
                    "Forma de pagamento": v["forma_pagamento"] or "",
                    "Canal": "Online" if v["canal"] == "online" else "Loja",
                    "Entrega": ("Entregue" if v["entregue"] else "Não entregue") if v["canal"] == "online" else "—",
                    "Desconto": brl(v["desconto"]),
                    "Valor total": brl(v["valor_total"]),
                    "Devolvido": brl(v["devolvido"]) if v["devolvido"] else "—",
                    "Tipo de receita": v["tipo_receita"] or "",
                }
                for v in vendas
            ],
            use_container_width=True,
            hide_index=True,
        )


def _aba_devolucao():
    st.caption(
        "Quando a cliente devolve peças: encontre a venda, marque as peças devolvidas e o valor "
        "devolvido a ela. As peças voltam para o estoque e o valor sai da receita na data da devolução."
    )
    mensagem = st.session_state.pop("devolucao_sucesso", None)
    if mensagem:
        st.success(mensagem)
    versao = st.session_state.setdefault("devolucao_versao", 0)
    hoje = hoje_brasil()

    col_busca, col_periodo = st.columns([2, 1])
    busca = col_busca.text_input(
        "Buscar venda (nº da venda, código da peça ou nome da cliente)",
        placeholder="ex: 35, Maria",
        key=f"dev_busca_{versao}",
    )
    periodo = col_periodo.date_input(
        "Vendas feitas entre", value=(hoje - timedelta(days=90), hoje), format="DD/MM/YYYY",
        key=f"dev_periodo_{versao}",
    )
    if not isinstance(periodo, (tuple, list)) or len(periodo) != 2:
        st.info("Selecione a data inicial e a final.")
        return
    inicio, fim = periodo

    itens = itens_vendidos(inicio, fim)
    termo = busca.strip()
    if termo:
        numeros = [t.lstrip("#") for t in re.split(r"[\s,;]+", termo) if t]
        if numeros and all(n.isdigit() for n in numeros):
            alvo = {int(n) for n in numeros}
            vendas_ok = {i["venda_id"] for i in itens if i["venda_id"] in alvo or i["produto_id"] in alvo}
        else:
            t = termo.casefold()
            vendas_ok = {
                i["venda_id"] for i in itens
                if t in (i["cliente"] or "").casefold()
                or t in (i["descricao"] or "").casefold()
                or t in (i["marca"] or "").casefold()
            }
        itens = [i for i in itens if i["venda_id"] in vendas_ok]

    por_venda = {}
    for i in itens:
        por_venda.setdefault(i["venda_id"], []).append(i)
    # só vendas que ainda têm peça para devolver
    por_venda = {v: its for v, its in por_venda.items() if any(not i["devolucao_id"] for i in its)}
    if not por_venda:
        st.info("Nenhuma venda com peças para devolver nesse filtro.")
    else:
        opcoes = {}
        for venda_id, its in por_venda.items():
            primeiro = its[0]
            rotulo = (
                f"Venda #{venda_id} — {fmt_data(primeiro['data_venda'])} — "
                f"{primeiro['cliente'] or 'cliente não informada'} — {len(its)} peça(s) — {brl(primeiro['valor_total'])}"
            )
            if any(i["devolucao_id"] for i in its):
                rotulo += " — já tem devolução"
            opcoes[rotulo] = venda_id
        escolha = st.selectbox("Venda", list(opcoes), key=f"dev_venda_{versao}_{termo}_{inicio}_{fim}")
        venda_id = opcoes[escolha]
        _formulario_devolucao(venda_id, por_venda[venda_id], versao, hoje)

    _devolucoes_recentes()


def _formulario_devolucao(venda_id, itens_venda, versao, hoje):
    venda = itens_venda[0]
    sugeridos = valores_sugeridos(itens_venda, venda["valor_total"])
    disponiveis = [i for i in itens_venda if not i["devolucao_id"]]
    devolvidas = [i for i in itens_venda if i["devolucao_id"]]

    if venda["desconto"]:
        st.caption(
            f"Esta venda teve desconto de {brl(venda['desconto'])}: o valor sugerido de cada peça "
            "já desconta a parte dela no desconto. Dá para ajustar."
        )
    editado = st.data_editor(
        pd.DataFrame(
            [
                {
                    "item_venda_id": i["item_venda_id"],
                    "devolver": len(disponiveis) == 1,
                    "codigo": i["produto_id"],
                    "descricao": i["descricao"],
                    "marca": i["marca"] or "",
                    "tamanho": i["tamanho"] or "",
                    "preco_vendido": float(i["preco_vendido"] or 0),
                    "valor_devolver": float(sugeridos[i["item_venda_id"]]),
                }
                for i in disponiveis
            ]
        ),
        hide_index=True,
        use_container_width=True,
        disabled=["codigo", "descricao", "marca", "tamanho", "preco_vendido"],
        key=f"dev_itens_{versao}_{venda_id}",
        column_order=["devolver", "codigo", "descricao", "marca", "tamanho", "preco_vendido", "valor_devolver"],
        column_config={
            "item_venda_id": None,
            "devolver": st.column_config.CheckboxColumn("Devolver?"),
            "codigo": st.column_config.NumberColumn("Código", format="%d"),
            "descricao": st.column_config.TextColumn("Descrição"),
            "marca": st.column_config.TextColumn("Marca"),
            "tamanho": st.column_config.TextColumn("Tamanho"),
            "preco_vendido": st.column_config.NumberColumn("Preço vendido (R$)", format="%.2f"),
            "valor_devolver": st.column_config.NumberColumn(
                "Valor devolvido (R$)", min_value=0.0, step=1.0, format="%.2f",
                help="Quanto volta para a cliente por esta peça (0 se for troca sem reembolso).",
            ),
        },
    )
    if devolvidas:
        st.caption("Já devolvidas antes: " + ", ".join(f"#{i['produto_id']} {i['descricao']}" for i in devolvidas))

    marcadas = editado[editado["devolver"] == True]  # noqa: E712
    col1, col2 = st.columns(2)
    data_devolucao = col1.date_input(
        "Data da devolução", value=hoje, min_value=venda["data_venda"], max_value=hoje,
        format="DD/MM/YYYY", key=f"dev_data_{versao}_{venda_id}",
    )
    forma = col2.selectbox("Como o valor foi devolvido", FORMAS_REEMBOLSO, key=f"dev_forma_{versao}_{venda_id}")
    motivo = st.text_input("Motivo (opcional)", placeholder="ex: não serviu, defeito", key=f"dev_motivo_{versao}_{venda_id}")

    total = sum((dec(v) for v in marcadas["valor_devolver"].fillna(0)), dec(0))
    ja_devolvido = dec(ja_devolvido_por_venda([venda_id]).get(venda_id, 0))
    limite = dec(venda["valor_total"]) - ja_devolvido
    m1, m2 = st.columns(2)
    m1.metric("Peças devolvidas", len(marcadas))
    m2.metric("Valor devolvido à cliente", brl(total))

    erro = None
    if marcadas["valor_devolver"].isna().any():
        erro = "Informe o valor devolvido de cada peça marcada (pode ser 0)."
    elif total > limite:
        erro = f"O valor devolvido ({brl(total)}) passa do que ainda pode ser devolvido desta venda ({brl(limite)})."
    if erro:
        st.error(erro)

    if st.button(
        "Registrar devolução", type="primary", disabled=marcadas.empty or bool(erro),
        key=f"dev_registrar_{versao}_{venda_id}",
    ):
        try:
            devolucao_id = registrar_devolucao(
                venda_id,
                [(int(r["item_venda_id"]), dec(r["valor_devolver"])) for _, r in marcadas.iterrows()],
                data_devolucao, forma, motivo.strip(), st.session_state.get("usuario"),
            )
        except DevolucaoInvalidaError as e:
            st.error(str(e))
        except Exception as e:
            st.error(f"Não foi possível registrar a devolução. Nada foi gravado. Erro: {e}")
        else:
            st.session_state["devolucao_sucesso"] = (
                f"Devolução #{devolucao_id} registrada: {len(marcadas)} peça(s) de volta ao estoque "
                f"({', '.join('#' + str(int(c)) for c in marcadas['codigo'])}), {brl(total)} devolvidos."
            )
            st.session_state["devolucao_versao"] = versao + 1
            st.rerun()


def _devolucoes_recentes():
    st.divider()
    st.subheader("Devoluções recentes")
    recentes = devolucoes_recentes()
    if not recentes:
        st.info("Nenhuma devolução registrada ainda.")
        return
    st.dataframe(
        [
            {
                "Devolução": f"#{d['id']}",
                "Data": fmt_data(d["data"]),
                "Venda": f"#{d['venda_id']}",
                "Cliente": d["cliente"] or "",
                "Peças (código)": d["pecas"],
                "Valor devolvido": brl(d["valor_devolvido"]),
                "Forma": d["forma_reembolso"],
                "Motivo": d["motivo"] or "",
                "Registrada por": d["criado_por"] or "",
            }
            for d in recentes
        ],
        use_container_width=True,
        hide_index=True,
    )
    canceláveis = {
        f"Devolução #{d['id']} — venda #{d['venda_id']} — peças {d['pecas']} — {brl(d['valor_devolvido'])}": d["id"]
        for d in recentes if d["pode_cancelar"]
    }
    if canceláveis:
        with st.expander("Cancelar uma devolução registrada por engano"):
            st.caption("As peças voltam a constar como vendidas. Não dá para cancelar se alguma já foi vendida de novo.")
            escolha = st.selectbox("Devolução", list(canceláveis), key="dev_cancelar_sel")
            if st.button("Cancelar devolução", key="dev_cancelar_btn"):
                try:
                    cancelar_devolucao(canceláveis[escolha])
                except DevolucaoInvalidaError as e:
                    st.error(str(e))
                else:
                    st.session_state["devolucao_sucesso"] = "Devolução cancelada; as peças voltaram a constar como vendidas."
                    st.rerun()


def _aba_fiado():
    st.caption(
        f"Vendas com a forma de pagamento \"{FORMA_FIADO}\". Quando a cliente pagar (tudo ou uma parte), "
        "registre aqui: o valor abate primeiro as compras mais antigas dela."
    )
    mensagem = st.session_state.pop("fiado_sucesso", None)
    if mensagem:
        st.success(mensagem)
    versao = st.session_state.setdefault("fiado_versao", 0)
    hoje = hoje_brasil()

    em_aberto = vendas_fiado_em_aberto()
    por_cliente = resumo_por_cliente(em_aberto)
    total = sum((v["saldo"] for v in em_aberto), dec(0))
    c1, c2, c3 = st.columns(3)
    with c1.container(border=True):
        st.metric("Total a receber", brl(total))
    with c2.container(border=True):
        st.metric("Clientes devendo", len(por_cliente), help=f"{len(em_aberto)} venda(s) em aberto")
    with c3.container(border=True):
        st.metric("Recebido este mês", brl(recebido_no_periodo(hoje.replace(day=1), hoje)))

    if not em_aberto:
        st.success("Nenhuma venda fiado em aberto.")
    else:
        st.subheader("Quem está devendo")
        st.dataframe(
            [
                {
                    "Cliente": g["cliente"],
                    "Vendas em aberto": g["vendas"],
                    "Deve": brl(g["saldo"]),
                    "Compra mais antiga": fmt_data(g["mais_antiga"]),
                    "Dias em aberto": (hoje - g["mais_antiga"]).days,
                }
                for g in por_cliente
            ],
            use_container_width=True,
            hide_index=True,
        )

        st.subheader("Registrar pagamento")
        opcoes = {f"{g['cliente']} — deve {brl(g['saldo'])}": g for g in por_cliente}
        escolha = st.selectbox("Cliente", list(opcoes), key=f"fiado_cliente_{versao}")
        g = opcoes[escolha]
        vendas_cliente = [v for v in em_aberto if chave_cliente(v["cliente"]) == chave_cliente(g["cliente"])]
        col1, col2, col3 = st.columns(3)
        valor = col1.number_input(
            "Valor pago (R$)", min_value=0.0, max_value=float(g["saldo"]), value=float(g["saldo"]),
            step=1.0, format="%.2f", key=f"fiado_valor_{versao}_{escolha}",
        )
        data_pagto = col2.date_input("Data do pagamento", value=hoje, max_value=hoje, format="DD/MM/YYYY",
                                     key=f"fiado_data_{versao}")
        forma = col3.selectbox("Forma", FORMAS_RECEBIMENTO, key=f"fiado_forma_{versao}")
        obs = st.text_input("Observação (opcional)", key=f"fiado_obs_{versao}")

        partes = distribuir_pagamento(vendas_cliente, valor) if valor > 0 else []
        saldo_por_venda = {v["id"]: v["saldo"] for v in vendas_cliente}
        abatido = dict(partes)
        st.dataframe(
            [
                {
                    "Venda": f"#{v['id']}",
                    "Data": fmt_data(v["data"]),
                    "Valor da venda": brl(v["valor_total"]),
                    "Já pago / devolvido": brl(dec(v["recebido"]) + dec(v["devolvido"])),
                    "Devia": brl(saldo_por_venda[v["id"]]),
                    "Este pagamento": brl(abatido.get(v["id"], 0)) if abatido.get(v["id"]) else "—",
                    "Fica devendo": brl(saldo_por_venda[v["id"]] - abatido.get(v["id"], dec(0))),
                }
                for v in vendas_cliente
            ],
            use_container_width=True,
            hide_index=True,
        )
        if st.button("Registrar pagamento", type="primary", disabled=valor <= 0, key=f"fiado_registrar_{versao}"):
            try:
                feitas = registrar_recebimento(
                    g["cliente"], valor, data_pagto, forma, obs.strip(), st.session_state.get("usuario")
                )
            except RecebimentoInvalidoError as e:
                st.error(str(e))
            else:
                restante = g["saldo"] - dec(valor)
                st.session_state["fiado_sucesso"] = (
                    f"Pagamento de {brl(valor)} de {g['cliente']} registrado "
                    f"(vendas {', '.join('#' + str(vid) for vid, _ in feitas)}). "
                    + (f"Ainda deve {brl(restante)}." if restante > 0 else "Tudo quitado!")
                )
                st.session_state["fiado_versao"] = versao + 1
                st.rerun()

    recentes = recebimentos_recentes()
    if recentes:
        st.divider()
        st.subheader("Pagamentos recebidos")
        st.dataframe(
            [
                {
                    "Data": fmt_data(r["data"]),
                    "Cliente": r["cliente"],
                    "Valor": brl(r["valor"]),
                    "Forma": r["forma"],
                    "Vendas": r["vendas"],
                    "Observação": r["observacao"] or "",
                    "Registrado por": r["criado_por"] or "",
                }
                for r in recentes
            ],
            use_container_width=True,
            hide_index=True,
        )
        with st.expander("Excluir um pagamento registrado por engano"):
            st.caption("O valor volta a constar como devido pela cliente.")
            opcoes_rec = {
                f"{fmt_data(r['data'])} — {r['cliente']} — {brl(r['valor'])} ({r['vendas']})": r["lote"]
                for r in recentes
            }
            escolha_rec = st.selectbox("Pagamento", list(opcoes_rec), key="fiado_excluir_sel")
            if st.button("Excluir pagamento", key="fiado_excluir_btn"):
                excluir_recebimento(opcoes_rec[escolha_rec])
                st.session_state["fiado_sucesso"] = "Pagamento excluído."
                st.rerun()


def _aba_entregas():
    st.caption("Vendas online ainda não entregues. Marque as que foram entregues.")
    mensagem = st.session_state.pop("entrega_sucesso", None)
    if mensagem:
        st.success(mensagem)
    versao = st.session_state.setdefault("entrega_versao", 0)
    hoje = hoje_brasil()

    pendentes = vendas_online(entregue=False)
    if not pendentes:
        st.success("Nenhuma venda online aguardando entrega.")
    else:
        st.metric("Aguardando entrega", len(pendentes), help=f"Somando {brl(sum(dec(v['valor_total']) for v in pendentes))}")
        editado = st.data_editor(
            pd.DataFrame(
                [
                    {
                        "id": v["id"],
                        "entregar": False,
                        "venda": f"#{v['id']}",
                        "data": fmt_data(v["data"]),
                        "dias": (hoje - v["data"]).days,
                        "cliente": v["cliente"] or "",
                        "pecas": v["pecas"] or "",
                        "valor": float(v["valor_total"]),
                    }
                    for v in pendentes
                ]
            ),
            hide_index=True,
            use_container_width=True,
            num_rows="fixed",
            disabled=["venda", "data", "dias", "cliente", "pecas", "valor"],
            key=f"entregas_{versao}",
            column_config={
                "id": None,
                "entregar": st.column_config.CheckboxColumn("Entregue?"),
                "venda": st.column_config.TextColumn("Venda"),
                "data": st.column_config.TextColumn("Data da venda"),
                "dias": st.column_config.NumberColumn("Dias esperando", format="%d"),
                "cliente": st.column_config.TextColumn("Cliente"),
                "pecas": st.column_config.TextColumn("Peças", width="large"),
                "valor": st.column_config.NumberColumn("Valor (R$)", format="%.2f"),
            },
        )
        marcadas = [int(i) for i in editado.loc[editado["entregar"] == True, "id"]]  # noqa: E712
        data_entrega = st.date_input("Data da entrega", value=hoje, max_value=hoje, format="DD/MM/YYYY",
                                     key=f"entrega_data_{versao}")
        if st.button(
            f"Marcar {len(marcadas)} como entregue(s)" if marcadas else "Marcar como entregue",
            type="primary", disabled=not marcadas, key=f"entrega_btn_{versao}",
        ):
            feitas = marcar_entregues(marcadas, data_entrega)
            st.session_state["entrega_sucesso"] = f"{feitas} venda(s) marcada(s) como entregue(s)."
            st.session_state["entrega_versao"] = versao + 1
            st.rerun()

    entregues = vendas_online(entregue=True, limite=30)
    if entregues:
        st.divider()
        st.subheader("Entregues recentemente")
        st.dataframe(
            [
                {
                    "Venda": f"#{v['id']}",
                    "Data da venda": fmt_data(v["data"]),
                    "Entregue em": fmt_data(v["data_entrega"]),
                    "Cliente": v["cliente"] or "",
                    "Peças": v["pecas"] or "",
                    "Valor": brl(v["valor_total"]),
                }
                for v in entregues
            ],
            use_container_width=True,
            hide_index=True,
        )
        with st.expander("Marcou como entregue por engano?"):
            opcoes = {f"Venda #{v['id']} — {v['cliente'] or 'sem cliente'} — entregue em {fmt_data(v['data_entrega'])}": v["id"] for v in entregues}
            escolha = st.selectbox("Venda", list(opcoes), key="entrega_desfazer_sel")
            if st.button("Voltar para não entregue", key="entrega_desfazer_btn"):
                desfazer_entrega(opcoes[escolha])
                st.session_state["entrega_sucesso"] = "A venda voltou para a lista de entregas pendentes."
                st.rerun()


with aba_venda:
    if mensagem_venda:
        st.success(mensagem_venda)
    _aba_registrar_venda()
    _vendas_recentes()

with aba_fiado:
    _aba_fiado()

with aba_entregas:
    _aba_entregas()

with aba_devolucao:
    _aba_devolucao()
