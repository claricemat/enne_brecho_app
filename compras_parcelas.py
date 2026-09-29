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


# ----------------------------------------------------------------
# Excluir compra (bazar, outros) / desfazer compra de fornecedora
# ----------------------------------------------------------------
class CompraNaoPodeSerExcluidaError(Exception):
    """A compra tem peça vendida ou parcela paga no controle de pagamentos."""


def compras_para_excluir(limite=60):
    return run_query(
        """
        SELECT c.id, c.data_aceite, c.valor_total, tc.nome AS tipo_compra,
               COALESCE(f.nome, '—') AS fornecedora,
               (SELECT a.id FROM avaliacao a WHERE a.compra_id = c.id LIMIT 1) AS avaliacao_id
        FROM compra c
        JOIN tipo_compra tc ON tc.id = c.tipo_compra_id
        LEFT JOIN fornecedora f ON f.id = c.fornecedora_id
        ORDER BY c.data_aceite DESC, c.id DESC
        LIMIT %s
        """,
        (limite,),
    )


def _impedimentos(consultar, compra_id):
    """Motivos que impedem excluir/desfazer a compra (lista vazia = pode)."""
    vendidas = consultar(
        """
        SELECT DISTINCT p.id FROM produto p
        JOIN item_venda iv ON iv.produto_id = p.id
        WHERE p.compra_id = %s ORDER BY p.id
        """,
        (compra_id,),
    )
    pagas = consultar(
        "SELECT COUNT(*) AS qtd FROM compra_parcela WHERE compra_id = %s AND baixa_id IS NOT NULL",
        (compra_id,),
    )[0]["qtd"]
    motivos = []
    if vendidas:
        codigos = ", ".join(f"#{v['id']}" for v in vendidas[:10]) + ("…" if len(vendidas) > 10 else "")
        motivos.append(
            f"{len(vendidas)} peça(s) desta compra já foram vendidas ({codigos}). "
            "Uma compra com peça vendida não pode ser apagada."
        )
    if pagas:
        motivos.append(
            f"{pagas} parcela(s) já foram pagas no Controle de pagamentos (peças). "
            "Apagar a compra deixaria esse pagamento sem compra."
        )
    return motivos


def analisar_exclusao(compra_id):
    """O que acontece se a compra for excluída/desfeita, e o que impede."""
    pecas = run_query("SELECT COUNT(*) AS qtd FROM produto WHERE compra_id = %s", (compra_id,))[0]["qtd"]
    avaliacao = run_query("SELECT id FROM avaliacao WHERE compra_id = %s", (compra_id,))
    return {
        "pecas": pecas,
        "avaliacao_id": avaliacao[0]["id"] if avaliacao else None,
        "impedimentos": _impedimentos(lambda sql, params: run_query(sql, params), compra_id),
    }


def excluir_ou_desfazer_compra(compra_id):
    """Apaga a compra, suas parcelas e suas peças (com as movimentações de estoque).
    Se ela veio de uma avaliação, a avaliação volta a ficar pendente.
    Tudo numa transação. Retorna o id da avaliação reaberta (ou None)."""
    with transacao() as executar:
        existe = executar("SELECT id FROM compra WHERE id = %s FOR UPDATE", (compra_id,), fetch=True)
        if not existe:
            raise CompraNaoPodeSerExcluidaError("Essa compra não existe mais (talvez já tenha sido apagada).")
        executar("SELECT id FROM produto WHERE compra_id = %s FOR UPDATE", (compra_id,))
        motivos = _impedimentos(lambda sql, params: executar(sql, params, fetch=True), compra_id)
        if motivos:
            raise CompraNaoPodeSerExcluidaError(" ".join(motivos))

        reaberta = executar(
            """
            UPDATE avaliacao SET status = 'pendente', proposta_aceita = NULL, compra_id = NULL
            WHERE compra_id = %s RETURNING id
            """,
            (compra_id,),
            fetch=True,
        )
        executar(
            "DELETE FROM movimentacao_estoque WHERE produto_id IN (SELECT id FROM produto WHERE compra_id = %s)",
            (compra_id,),
        )
        executar("DELETE FROM produto WHERE compra_id = %s", (compra_id,))
        executar("DELETE FROM compra_parcela WHERE compra_id = %s", (compra_id,))
        executar("DELETE FROM compra WHERE id = %s", (compra_id,))
    return reaberta[0]["id"] if reaberta else None


def excluir_avaliacao(avaliacao_id):
    """Apaga uma avaliação pendente ou recusada (os itens vão junto)."""
    feito = run_query(
        "DELETE FROM avaliacao WHERE id = %s AND status IN ('pendente', 'recusada') AND compra_id IS NULL RETURNING id",
        (avaliacao_id,),
    )
    return bool(feito)
