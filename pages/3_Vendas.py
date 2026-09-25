import re
from datetime import timedelta

import pandas as pd
import streamlit as st

from db import run_query, transacao
from branding import aplicar_logo
from auth import exigir_login, botao_logout
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

aba_venda, aba_devolucao = st.tabs(["Registrar venda", "Devolução de peças"])


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

    df_estoque = pd.DataFrame(produtos)
    df_estoque.insert(0, "vender", False)
    # o banco devolve Decimal; a tabela e o desconto trabalham com número comum (float)
    df_estoque["preco_venda"] = pd.to_numeric(df_estoque["preco_venda"], errors="coerce").astype(float)
    df_estoque["preco_vendido"] = df_estoque["preco_venda"]

    busca = st.text_input(
        "Buscar peça (por código/ID, descrição ou marca)",
        placeholder="ex: 42, camisa azul, Farm",
        help="O código é o número que está na etiqueta. Dá para digitar vários de uma vez: 12, 15, 20.",
    )
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
        "Marque as peças vendidas nessa transação. O preço vem pré-preenchido com o "
        "preço de venda, mas pode ser ajustado (ex: negociação item a item)."
    )

    if df_estoque.empty:
        st.warning("Nenhuma peça em estoque bate com essa busca.")
        return

    editado = st.data_editor(
        df_estoque,
        use_container_width=True,
        hide_index=True,
        disabled=["id", "descricao", "marca", "tipo_peca", "tamanho", "preco_venda"],
        key=f"editor_venda_{st.session_state.venda_version}_{busca}",
        column_config={
            "vender": st.column_config.CheckboxColumn("Vender?"),
            "marca": st.column_config.TextColumn("Marca"),
            "preco_vendido": st.column_config.NumberColumn(
                "Preço vendido (R$)", min_value=0.0, step=1.0
            ),
        },
    )

    selecionados = editado[editado["vender"]]

    col1, col2 = st.columns(2)
    cliente = col1.text_input("Cliente (opcional)")
    forma_pagamento = col2.selectbox(
        "Forma de pagamento",
        ["Dinheiro", "Pix", "Cartão de Crédito", "Cartão de Débito", "Crédito Loja", "Outro"],
    )

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

    if st.button("Registrar venda", type="primary", disabled=selecionados.empty):
        subtotal = round(selecionados["preco_vendido"].sum(), 2)
        valor_total = round(max(subtotal - desconto, 0), 2)

        try:
            # tudo numa transação: ou a venda inteira é gravada, ou nada
            with transacao() as executar:
                venda_id = executar(
                    "INSERT INTO venda (forma_pagamento, valor_total, cliente, desconto, plano_conta_id) "
                    "VALUES (%s, %s, %s, %s, %s) RETURNING id",
                    (forma_pagamento, valor_total, cliente.strip() or None, desconto, plano_conta_id),
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

        st.session_state["venda_sucesso"] = (
            f"Venda #{venda_id} registrada — {len(selecionados)} peça(s), "
            f"total {brl(valor_total)} (desconto de {brl(desconto)})."
        )
        st.session_state.venda_version += 1
        st.rerun()


def _vendas_recentes():
    st.divider()
    st.subheader("Vendas recentes")
    vendas = run_query(
        """
        SELECT v.id, v.data_venda, v.cliente, v.forma_pagamento, v.desconto, v.valor_total,
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


with aba_venda:
    if mensagem_venda:
        st.success(mensagem_venda)
    _aba_registrar_venda()
    _vendas_recentes()

with aba_devolucao:
    _aba_devolucao()
