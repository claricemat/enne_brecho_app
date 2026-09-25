"""Devolução de peças vendidas.

A peça devolvida volta para o estoque (status 'em_estoque' + movimentação de
entrada) e o valor devolvido à cliente fica registrado para ser descontado da
receita na data da devolução.
"""

from decimal import Decimal

from db import run_query, transacao

FORMAS_REEMBOLSO = [
    "Dinheiro",
    "Pix",
    "Estorno no cartão",
    "Crédito na loja (vale-troca)",
    "Troca por outra peça",
    "Sem reembolso",
    "Outro",
]

CENTAVO = Decimal("0.01")


class DevolucaoInvalidaError(Exception):
    """A devolução não pode ser gravada (peça já devolvida, valor acima da venda...)."""


def itens_vendidos(inicio, fim):
    """Todas as peças vendidas no período, com a venda e se já foram devolvidas."""
    return run_query(
        """
        SELECT v.id AS venda_id, v.data_venda::date AS data_venda, v.cliente, v.forma_pagamento,
               v.valor_total, v.desconto,
               iv.id AS item_venda_id, iv.produto_id, iv.preco_vendido,
               p.descricao, p.marca, p.tamanho, p.status AS produto_status,
               di.devolucao_id
        FROM venda v
        JOIN item_venda iv ON iv.venda_id = v.id
        JOIN produto p ON p.id = iv.produto_id
        LEFT JOIN devolucao_item di ON di.item_venda_id = iv.id
        WHERE v.data_venda::date BETWEEN %s AND %s
        ORDER BY v.data_venda DESC, v.id DESC, iv.id
        """,
        (inicio, fim),
    )


def ja_devolvido_por_venda(venda_ids):
    if not venda_ids:
        return {}
    linhas = run_query(
        "SELECT venda_id, SUM(valor_devolvido) AS total FROM devolucao WHERE venda_id = ANY(%s) GROUP BY venda_id",
        (list(venda_ids),),
    )
    return {l["venda_id"]: l["total"] for l in linhas}


def valores_sugeridos(itens_da_venda, valor_total_venda):
    """Valor a devolver de cada peça = preço vendido menos a parte dela no desconto
    da venda (desconto rateado pelo preço). Retorna {item_venda_id: Decimal}."""
    subtotal = sum((Decimal(i["preco_vendido"] or 0) for i in itens_da_venda), Decimal("0"))
    total = Decimal(valor_total_venda or 0)
    if subtotal <= 0:
        return {i["item_venda_id"]: Decimal("0.00") for i in itens_da_venda}
    return {
        i["item_venda_id"]: (Decimal(i["preco_vendido"] or 0) * total / subtotal).quantize(CENTAVO)
        for i in itens_da_venda
    }


def registrar_devolucao(venda_id, itens, data, forma_reembolso, motivo, usuario):
    """itens: lista de (item_venda_id, valor_devolvido). Tudo numa transação.
    Retorna o id da devolução."""
    ids = [i for i, _ in itens]
    valor_total = sum((Decimal(str(v)).quantize(CENTAVO) for _, v in itens), Decimal("0"))

    with transacao() as executar:
        venda = executar(
            "SELECT id, valor_total, data_venda::date AS data FROM venda WHERE id = %s FOR UPDATE",
            (venda_id,),
            fetch=True,
        )
        if not venda:
            raise DevolucaoInvalidaError("Venda não encontrada.")
        venda = venda[0]
        if data < venda["data"]:
            raise DevolucaoInvalidaError("A data da devolução não pode ser antes da data da venda.")

        linhas = executar(
            """
            SELECT iv.id, iv.venda_id, iv.produto_id, p.status, di.id AS ja_devolvido
            FROM item_venda iv
            JOIN produto p ON p.id = iv.produto_id
            LEFT JOIN devolucao_item di ON di.item_venda_id = iv.id
            WHERE iv.id = ANY(%s)
            FOR UPDATE OF p
            """,
            (ids,),
            fetch=True,
        )
        if len(linhas) != len(ids) or any(l["venda_id"] != venda_id for l in linhas):
            raise DevolucaoInvalidaError("Alguma peça escolhida não é desta venda.")
        if any(l["ja_devolvido"] or l["status"] != "vendido" for l in linhas):
            raise DevolucaoInvalidaError(
                "Alguma peça já foi devolvida ou já está no estoque (talvez pela outra pessoa). "
                "Nada foi gravado — atualize a página."
            )

        anterior = executar(
            "SELECT COALESCE(SUM(valor_devolvido), 0) AS total FROM devolucao WHERE venda_id = %s",
            (venda_id,),
            fetch=True,
        )[0]["total"]
        if anterior + valor_total > Decimal(venda["valor_total"] or 0):
            raise DevolucaoInvalidaError(
                "O valor devolvido passa do valor pago na venda "
                f"(venda: R$ {venda['valor_total']}, já devolvido antes: R$ {anterior})."
            )

        devolucao_id = executar(
            """
            INSERT INTO devolucao (venda_id, data, valor_devolvido, forma_reembolso, motivo, criado_por)
            VALUES (%s, %s, %s, %s, %s, %s) RETURNING id
            """,
            (venda_id, data, valor_total, forma_reembolso, motivo or None, usuario),
            fetch=True,
        )[0]["id"]

        produto_por_item = {l["id"]: l["produto_id"] for l in linhas}
        for item_id, valor in itens:
            produto_id = produto_por_item[item_id]
            executar(
                "INSERT INTO devolucao_item (devolucao_id, item_venda_id, produto_id, valor) VALUES (%s, %s, %s, %s)",
                (devolucao_id, item_id, produto_id, Decimal(str(valor)).quantize(CENTAVO)),
            )
            executar("UPDATE produto SET status = 'em_estoque' WHERE id = %s", (produto_id,))
            executar(
                "INSERT INTO movimentacao_estoque (produto_id, tipo, observacao) VALUES (%s, 'entrada', %s)",
                (produto_id, f"Entrada por devolução #{devolucao_id} (venda #{venda_id})"),
            )
    return devolucao_id


def devolucoes_recentes(limite=30):
    return run_query(
        """
        SELECT d.id, d.data, d.venda_id, v.cliente, d.valor_devolvido, d.forma_reembolso, d.motivo,
               d.criado_por,
               string_agg('#' || di.produto_id::text, ', ' ORDER BY di.produto_id) AS pecas,
               bool_and(p.status = 'em_estoque') AS pode_cancelar
        FROM devolucao d
        JOIN venda v ON v.id = d.venda_id
        JOIN devolucao_item di ON di.devolucao_id = d.id
        JOIN produto p ON p.id = di.produto_id
        GROUP BY d.id, v.cliente
        ORDER BY d.data DESC, d.id DESC
        LIMIT %s
        """,
        (limite,),
    )


def cancelar_devolucao(devolucao_id):
    """Desfaz uma devolução registrada por engano: as peças voltam a 'vendido'.
    Só é possível se nenhuma delas foi vendida de novo."""
    with transacao() as executar:
        pecas = executar(
            """
            SELECT p.id, p.status
            FROM devolucao_item di
            JOIN produto p ON p.id = di.produto_id
            WHERE di.devolucao_id = %s
            FOR UPDATE OF p
            """,
            (devolucao_id,),
            fetch=True,
        )
        if not pecas:
            raise DevolucaoInvalidaError("Devolução não encontrada (talvez já cancelada).")
        if any(p["status"] != "em_estoque" for p in pecas):
            raise DevolucaoInvalidaError(
                "Alguma peça desta devolução já foi vendida de novo — não dá para cancelar."
            )
        for p in pecas:
            executar("UPDATE produto SET status = 'vendido' WHERE id = %s", (p["id"],))
            executar(
                "INSERT INTO movimentacao_estoque (produto_id, tipo, observacao) VALUES (%s, 'saida', %s)",
                (p["id"], f"Devolução #{devolucao_id} cancelada"),
            )
        executar("DELETE FROM devolucao WHERE id = %s", (devolucao_id,))
