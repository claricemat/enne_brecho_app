"""Cobranças das fornecedoras e o alerta de quem está insistindo para receber.

Cada vez que uma fornecedora cobra, a cobrança é registrada (data + observação).
O alerta mostra as fornecedoras que cobraram e ainda têm parcelas em aberto,
contando só as cobranças feitas depois do último pagamento a ela: quando ela
é paga, o alerta some e a contagem recomeça.
"""

from db import run_query


def registrar_cobranca(fornecedora_id, data, observacao, usuario):
    run_query(
        "INSERT INTO cobranca_fornecedora (fornecedora_id, data, observacao, criado_por) VALUES (%s, %s, %s, %s)",
        (fornecedora_id, data, observacao or None, usuario),
        fetch=False,
    )


def excluir_cobranca(cobranca_id):
    run_query("DELETE FROM cobranca_fornecedora WHERE id = %s", (cobranca_id,), fetch=False)


def alertas(hoje):
    """Fornecedoras que cobraram (depois do último pagamento) e têm parcelas em aberto.
    Ordem: quem cobrou mais vezes, depois quem tem mais em atraso."""
    return run_query(
        """
        WITH ultimo_pagamento AS (
            SELECT fornecedora_id, MAX(data_pagamento) AS data
            FROM baixa_compra GROUP BY fornecedora_id
        ),
        cobrancas AS (
            SELECT cf.fornecedora_id, COUNT(*) AS qtd, MAX(cf.data) AS ultima, MIN(cf.data) AS primeira
            FROM cobranca_fornecedora cf
            LEFT JOIN ultimo_pagamento up ON up.fornecedora_id = cf.fornecedora_id
            WHERE up.data IS NULL OR cf.data > up.data
            GROUP BY cf.fornecedora_id
        ),
        aberto AS (
            SELECT c.fornecedora_id,
                   SUM(p.valor) AS em_aberto,
                   COALESCE(SUM(p.valor) FILTER (WHERE p.data_vencimento < %s), 0) AS em_atraso,
                   MIN(p.data_vencimento) AS proximo_vencimento,
                   COUNT(*) AS parcelas
            FROM compra_parcela p
            JOIN compra c ON c.id = p.compra_id
            WHERE p.status = 'pendente' AND c.fornecedora_id IS NOT NULL
            GROUP BY c.fornecedora_id
        )
        SELECT f.id AS fornecedora_id, f.nome AS fornecedora, f.contato,
               co.qtd AS cobrancas, co.ultima, co.primeira,
               a.em_aberto, a.em_atraso, a.proximo_vencimento, a.parcelas
        FROM cobrancas co
        JOIN aberto a ON a.fornecedora_id = co.fornecedora_id
        JOIN fornecedora f ON f.id = co.fornecedora_id
        ORDER BY co.qtd DESC, a.em_atraso DESC, co.ultima DESC
        """,
        (hoje,),
    )


def historico(limite=50):
    return run_query(
        """
        SELECT cf.id, cf.data, f.nome AS fornecedora, cf.observacao, cf.criado_por
        FROM cobranca_fornecedora cf
        JOIN fornecedora f ON f.id = cf.fornecedora_id
        ORDER BY cf.data DESC, cf.id DESC
        LIMIT %s
        """,
        (limite,),
    )


def nivel(qtd):
    """Texto curto para o alerta conforme o número de cobranças."""
    if qtd >= 3:
        return f"🔴 insistindo ({qtd} cobranças)"
    if qtd == 2:
        return "🟠 cobrou 2 vezes"
    return "🟡 cobrou 1 vez"
