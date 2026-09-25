"""Parcelas das compras de peças: consulta e edição (reparcelamento).

A situação da compra (paga / em aberto, próximo vencimento) é recalculada pelo
banco sempre que as parcelas mudam (ver migracao_parcelas_devolucao.sql).
"""

from db import run_query, transacao


class ParcelasAlteradasError(Exception):
    """As parcelas mudaram desde que a tela foi aberta (ex.: a outra sócia pagou uma)."""


def parcelas_da_compra(compra_id):
    return run_query(
        """
        SELECT id, numero, valor, data_vencimento, status, data_pagamento, baixa_id
        FROM compra_parcela
        WHERE compra_id = %s
        ORDER BY numero
        """,
        (compra_id,),
    )


def compras_em_aberto():
    return run_query(
        """
        SELECT c.id, COALESCE(f.nome, '—') AS fornecedora, tc.nome AS tipo_compra,
               c.data_aceite, c.valor_total, c.data_vencimento,
               COUNT(p.id) AS parcelas,
               COUNT(p.id) FILTER (WHERE p.status = 'pago') AS pagas
        FROM compra c
        JOIN tipo_compra tc ON tc.id = c.tipo_compra_id
        LEFT JOIN fornecedora f ON f.id = c.fornecedora_id
        JOIN compra_parcela p ON p.compra_id = c.id
        WHERE c.status = 'pendente'
        GROUP BY c.id, f.nome, tc.nome
        ORDER BY c.data_vencimento NULLS LAST, c.id
        """
    )


def reparcelar(compra_id, ids_pendentes_esperados, novas_parcelas):
    """Troca as parcelas EM ABERTO da compra pelas novas (as pagas não mudam).
    Depois renumera todas por ordem de vencimento.

    ids_pendentes_esperados: as parcelas em aberto que a tela mostrava; se no
    banco estiverem diferentes (alguém pagou ou editou), nada é gravado."""
    with transacao() as executar:
        atuais = executar(
            "SELECT id, status FROM compra_parcela WHERE compra_id = %s ORDER BY id FOR UPDATE",
            (compra_id,),
            fetch=True,
        )
        pendentes_agora = sorted(p["id"] for p in atuais if p["status"] == "pendente")
        if pendentes_agora != sorted(ids_pendentes_esperados):
            raise ParcelasAlteradasError(
                "As parcelas dessa compra mudaram desde que a tela foi aberta "
                "(talvez a outra pessoa pagou ou editou). Nada foi gravado — atualize a página."
            )

        executar("DELETE FROM compra_parcela WHERE id = ANY(%s)", (pendentes_agora,))
        proximo = max((p["id"] for p in atuais), default=0)  # só para números provisórios únicos
        for i, parcela in enumerate(novas_parcelas):
            executar(
                "INSERT INTO compra_parcela (compra_id, numero, valor, data_vencimento) VALUES (%s, %s, %s, %s)",
                (compra_id, 100000 + proximo + i, parcela["valor"], parcela["data_vencimento"]),
            )

        # numeração final 1, 2, 3... por ordem de vencimento (pagas e em aberto juntas)
        executar(
            """
            UPDATE compra_parcela p
            SET numero = r.n
            FROM (
                SELECT id, ROW_NUMBER() OVER (ORDER BY data_vencimento, numero) AS n
                FROM compra_parcela WHERE compra_id = %s
            ) r
            WHERE p.id = r.id
            """,
            (compra_id,),
        )
