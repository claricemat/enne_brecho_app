"""Edição de uma compra já registrada: preço de venda das peças.

Só as peças que ainda estão no estoque podem mudar de preço (as vendidas
guardam o preço da venda em item_venda e não são alteradas).
"""

from decimal import Decimal

from db import run_query, transacao

CENTAVO = Decimal("0.01")


class EdicaoCompraError(Exception):
    """Alguma peça foi vendida ou alterada enquanto a tela estava aberta."""


def _dec(valor):
    return Decimal(str(valor)).quantize(CENTAVO)


def compras_para_editar():
    """Compras que têm peças, da mais recente para a mais antiga."""
    return run_query(
        """
        SELECT c.id, tc.nome AS tipo_compra, COALESCE(f.nome, '—') AS fornecedora,
               c.data_aceite, c.valor_total, c.observacao,
               COUNT(p.id) AS pecas,
               COUNT(p.id) FILTER (WHERE p.status = 'em_estoque') AS em_estoque
        FROM compra c
        JOIN tipo_compra tc ON tc.id = c.tipo_compra_id
        LEFT JOIN fornecedora f ON f.id = c.fornecedora_id
        JOIN produto p ON p.compra_id = c.id
        GROUP BY c.id, tc.nome, f.nome
        ORDER BY c.data_aceite DESC, c.id DESC
        """
    )


def pecas_da_compra(compra_id):
    return run_query(
        """
        SELECT p.id, p.descricao, p.marca, tp.nome AS tipo_peca, p.tamanho,
               p.preco_custo, p.preco_venda, p.status
        FROM produto p
        LEFT JOIN tipo_peca tp ON tp.id = p.tipo_peca_id
        WHERE p.compra_id = %s
        ORDER BY p.id
        """,
        (compra_id,),
    )


def atualizar_precos_venda(compra_id, alteracoes):
    """alteracoes: [(produto_id, preco_antigo, preco_novo)]. Tudo numa transação:
    se alguma peça foi vendida ou teve o preço mudado por outra pessoa enquanto
    a tela estava aberta, nada é gravado. Retorna quantas peças mudaram."""
    if not alteracoes:
        return 0
    with transacao() as executar:
        for produto_id, antigo, novo in alteracoes:
            novo = _dec(novo)
            if novo <= 0:
                raise EdicaoCompraError(f"Peça #{produto_id}: o preço de venda precisa ser maior que zero.")
            feito = executar(
                """
                UPDATE produto SET preco_venda = %s
                WHERE id = %s AND compra_id = %s AND status = 'em_estoque'
                  AND preco_venda IS NOT DISTINCT FROM %s
                RETURNING id
                """,
                (novo, produto_id, compra_id, None if antigo is None else _dec(antigo)),
                fetch=True,
            )
            if not feito:
                raise EdicaoCompraError(
                    f"A peça #{produto_id} foi vendida ou alterada enquanto a tela estava aberta "
                    "(talvez pela outra pessoa). Nada foi gravado — atualize a página."
                )
    return len(alteracoes)
