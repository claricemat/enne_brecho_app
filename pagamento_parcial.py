"""Pagamento parcial de contas a pagar (despesas e parcelas de compras de peças).

A conta é dividida em duas: a parte paga e o restante, que continua em aberto.
As duas ficam com o vencimento original; mudar o vencimento do restante é opcional.
Ex.: aluguel de R$ 400,00 com vencimento no dia 10; paga R$ 250,00
-> fica R$ 250,00 pago e R$ 150,00 em aberto, os dois com vencimento no dia 10.
"""

from decimal import Decimal

from db import transacao

CENTAVO = Decimal("0.01")
SUFIXO_RESTANTE = " — restante"


class PagamentoParcialInvalidoError(Exception):
    """A conta mudou desde que a tela foi aberta, ou o valor não é válido."""


def _dec(valor):
    return Decimal(str(valor)).quantize(CENTAVO)


def descricao_restante(descricao):
    """'Aluguel' -> 'Aluguel — restante' (sem repetir o sufixo nos pagamentos seguintes)."""
    base = (descricao or "").strip()
    if base.endswith(SUFIXO_RESTANTE.strip()) or base.endswith(SUFIXO_RESTANTE):
        return base
    return (base + SUFIXO_RESTANTE) if base else "Restante"


def pagar_despesa_parcial(despesa_id, valor_esperado, valor_pago, vencimento_restante=None):
    """Registra o pagamento de parte de uma despesa pendente. A parte paga mantém
    a data (vencimento) da despesa; o restante também, a não ser que
    vencimento_restante seja informado. Retorna o id da nova despesa com o restante."""
    valor_esperado, valor_pago = _dec(valor_esperado), _dec(valor_pago)
    with transacao() as executar:
        linha = executar(
            "SELECT * FROM despesa WHERE id = %s FOR UPDATE", (despesa_id,), fetch=True
        )
        if not linha or linha[0]["status_pagamento"] != "pendente" or _dec(linha[0]["valor"]) != valor_esperado:
            raise PagamentoParcialInvalidoError(
                "Essa despesa foi paga ou alterada enquanto a tela estava aberta (talvez pela outra "
                "pessoa). Nada foi gravado — atualize a página."
            )
        d = linha[0]
        if not (Decimal("0") < valor_pago < valor_esperado):
            raise PagamentoParcialInvalidoError("O valor pago precisa ser maior que zero e menor que o valor da despesa.")

        # a parte paga mantém o vencimento da despesa
        executar(
            "UPDATE despesa SET valor = %s, status_pagamento = 'pago' WHERE id = %s",
            (valor_pago, despesa_id),
        )
        # o restante continua em aberto (mesma categoria e, se for parcela, mesma parcela)
        nova = executar(
            """
            INSERT INTO despesa (plano_conta_id, descricao, valor, data, status_pagamento,
                                 parcelamento_id, parcela_numero, parcela_total)
            VALUES (%s, %s, %s, %s, 'pendente', %s, %s, %s)
            RETURNING id
            """,
            (d["plano_conta_id"], descricao_restante(d["descricao"]), valor_esperado - valor_pago,
             vencimento_restante or d["data"], d["parcelamento_id"], d["parcela_numero"], d["parcela_total"]),
            fetch=True,
        )
    return nova[0]["id"]


def dividir_parcela_compra(executar, parcela_id, valor_esperado, valor_pago, vencimento_restante=None):
    """Dentro de uma transação já aberta: a parcela passa a valer só a parte que
    vai ser paga, e o restante vira uma parcela nova em aberto, com o mesmo
    vencimento (ou vencimento_restante, se informado). Depois as parcelas da
    compra são renumeradas por ordem de vencimento."""
    valor_esperado, valor_pago = _dec(valor_esperado), _dec(valor_pago)
    linha = executar(
        "SELECT compra_id, valor, status, data_vencimento FROM compra_parcela WHERE id = %s FOR UPDATE",
        (parcela_id,),
        fetch=True,
    )
    if not linha or linha[0]["status"] != "pendente" or _dec(linha[0]["valor"]) != valor_esperado:
        raise PagamentoParcialInvalidoError(
            "Essa parcela foi paga ou alterada enquanto a tela estava aberta. Nada foi gravado — atualize a página."
        )
    if not (Decimal("0") < valor_pago < valor_esperado):
        raise PagamentoParcialInvalidoError("O valor pago precisa ser maior que zero e menor que o valor da parcela.")
    compra_id = linha[0]["compra_id"]

    executar("UPDATE compra_parcela SET valor = %s WHERE id = %s", (valor_pago, parcela_id))
    executar(
        """
        INSERT INTO compra_parcela (compra_id, numero, valor, data_vencimento)
        VALUES (%s, (SELECT MAX(numero) + 1000 FROM compra_parcela WHERE compra_id = %s), %s, %s)
        """,
        (compra_id, compra_id, valor_esperado - valor_pago, vencimento_restante or linha[0]["data_vencimento"]),
    )
    # numeração 1, 2, 3... por vencimento (a parte paga vem antes do restante no mesmo dia)
    executar(
        """
        UPDATE compra_parcela p SET numero = r.n
        FROM (SELECT id, ROW_NUMBER() OVER (ORDER BY data_vencimento, numero) AS n
              FROM compra_parcela WHERE compra_id = %s) r
        WHERE p.id = r.id
        """,
        (compra_id,),
    )
