"""Controle das vendas em "Cliente fiel" (fiado) e das entregas das vendas online.

Fiado: a venda é registrada normalmente com a forma de pagamento "Cliente fiel"
(a venda inteira ou só uma parte dela, quando dividida em duas formas).
Quando a cliente paga (tudo ou uma parte), o valor abate primeiro as vendas
mais antigas dela, como num caderninho. Saldo devedor de cada venda =
valor em "Cliente fiel" − devoluções − recebimentos (a devolução abate
primeiro a parte que ainda não foi paga).
"""

import uuid
from decimal import Decimal

from db import run_query, transacao
from formatacao import brl

FORMA_FIADO = "Cliente fiel"
FORMAS_RECEBIMENTO = ["Dinheiro", "Pix", "Cartão de Crédito", "Cartão de Débito", "Outro"]
FORMAS_VENDA = ["Dinheiro", "Pix", "Cartão de Crédito", "Cartão de Débito", "Crédito Loja", FORMA_FIADO, "Outro"]

# formas de cada venda, para as consultas: "Pix|50.00;Dinheiro|30.00"
SQL_FORMAS_DA_VENDA = (
    "(SELECT string_agg(vp.forma || '|' || vp.valor::text, ';' ORDER BY vp.id) "
    "FROM venda_pagamento vp WHERE vp.venda_id = v.id)"
)

CENTAVO = Decimal("0.01")


class RecebimentoInvalidoError(Exception):
    """O pagamento não pode ser registrado (valor acima do que a cliente deve...)."""


def rotulo_formas(pagamentos):
    """[(forma, valor), ...] -> "Pix" ou "Pix + Dinheiro" (texto gravado em venda.forma_pagamento)."""
    return " + ".join(forma for forma, _ in pagamentos)


def detalhe_formas(texto, padrao=""):
    """'Pix|50.00;Dinheiro|30.00' -> 'Pix R$ 50,00 + Dinheiro R$ 30,00'.
    Com uma forma só, mostra só o nome dela."""
    if not texto:
        return padrao
    partes = [p.split("|", 1) for p in texto.split(";") if "|" in p]
    if len(partes) == 1:
        return partes[0][0]
    return " + ".join(f"{forma} {brl(Decimal(valor))}" for forma, valor in partes)


def chave_cliente(nome):
    """Mesmo nome, com ou sem espaços/maiúsculas diferentes, é a mesma cliente."""
    return " ".join(str(nome or "").split()).casefold()


# ----------------------------------------------------------------
# Fiado
# ----------------------------------------------------------------
_SQL_VENDAS_FIADO = """
    SELECT v.id, v.data_venda::date AS data, v.cliente, v.valor_total, v.canal,
           fp.valor AS valor_fiado,
           COALESCE((SELECT SUM(d.valor_devolvido) FROM devolucao d WHERE d.venda_id = v.id), 0) AS devolvido,
           COALESCE((SELECT SUM(r.valor) FROM venda_recebimento r WHERE r.venda_id = v.id), 0) AS recebido
    FROM venda v
    JOIN (SELECT venda_id, SUM(valor) AS valor FROM venda_pagamento
          WHERE forma = %s GROUP BY venda_id) fp ON fp.venda_id = v.id
"""


def _com_saldo(linhas):
    for l in linhas:
        saldo = Decimal(l["valor_fiado"]) - Decimal(l["devolvido"]) - Decimal(l["recebido"])
        l["saldo"] = max(saldo, Decimal("0"))
    return linhas


def vendas_fiado_em_aberto():
    """Vendas em fiado que ainda têm saldo a receber, da mais antiga para a mais nova."""
    linhas = _com_saldo(run_query(_SQL_VENDAS_FIADO + " ORDER BY v.data_venda, v.id", (FORMA_FIADO,)))
    return [l for l in linhas if l["saldo"] > 0]


def clientes_fiel():
    """Nomes das clientes que já compraram em fiado (a grafia mais recente de cada uma)."""
    linhas = run_query(
        "SELECT cliente FROM venda v WHERE cliente IS NOT NULL "
        "AND EXISTS (SELECT 1 FROM venda_pagamento p WHERE p.venda_id = v.id AND p.forma = %s) "
        "ORDER BY data_venda DESC, id DESC",
        (FORMA_FIADO,),
    )
    nomes = {}
    for l in linhas:
        nomes.setdefault(chave_cliente(l["cliente"]), " ".join(l["cliente"].split()))
    return sorted(nomes.values(), key=str.casefold)


def resumo_por_cliente(em_aberto):
    """Agrupa as vendas em aberto por cliente."""
    grupos = {}
    for v in em_aberto:
        g = grupos.setdefault(
            chave_cliente(v["cliente"]),
            {"cliente": " ".join(v["cliente"].split()), "vendas": 0, "saldo": Decimal("0"), "mais_antiga": v["data"]},
        )
        g["vendas"] += 1
        g["saldo"] += v["saldo"]
        g["mais_antiga"] = min(g["mais_antiga"], v["data"])
    return sorted(grupos.values(), key=lambda g: g["saldo"], reverse=True)


def distribuir_pagamento(vendas_da_cliente, valor):
    """Abate o valor nas vendas mais antigas primeiro. Retorna [(venda_id, valor)]."""
    restante = Decimal(str(valor)).quantize(CENTAVO)
    partes = []
    for v in sorted(vendas_da_cliente, key=lambda x: (x["data"], x["id"])):
        if restante <= 0:
            break
        parte = min(restante, v["saldo"])
        if parte > 0:
            partes.append((v["id"], parte))
            restante -= parte
    return partes


def registrar_recebimento(cliente, valor, data, forma, observacao, usuario):
    """Registra um pagamento da cliente, abatendo as vendas mais antigas primeiro.
    Tudo numa transação. Retorna [(venda_id, valor abatido)]."""
    valor = Decimal(str(valor)).quantize(CENTAVO)
    if valor <= 0:
        raise RecebimentoInvalidoError("Informe um valor maior que zero.")
    alvo = chave_cliente(cliente)
    with transacao() as executar:
        # trava as vendas em fiado (a outra pessoa pode estar registrando um pagamento junto)
        todas = _com_saldo(
            executar(_SQL_VENDAS_FIADO + " ORDER BY v.data_venda, v.id FOR UPDATE OF v", (FORMA_FIADO,), fetch=True)
        )
        vendas = [v for v in todas if v["saldo"] > 0 and chave_cliente(v["cliente"]) == alvo]
        devendo = sum((v["saldo"] for v in vendas), Decimal("0"))
        if not vendas:
            raise RecebimentoInvalidoError("Essa cliente não tem nada em aberto.")
        if valor > devendo:
            raise RecebimentoInvalidoError(
                f"O valor pago (R$ {valor}) é maior do que a cliente deve (R$ {devendo})."
            )
        lote = str(uuid.uuid4())
        partes = distribuir_pagamento(vendas, valor)
        for venda_id, parte in partes:
            executar(
                "INSERT INTO venda_recebimento (venda_id, data, valor, forma, lote, observacao, criado_por) "
                "VALUES (%s, %s, %s, %s, %s::uuid, %s, %s)",
                (venda_id, data, parte, forma, lote, observacao or None, usuario),
            )
    return partes


def recebimentos_recentes(limite=30):
    """Pagamentos recebidos (cada pagamento pode ter quitado mais de uma venda)."""
    return run_query(
        """
        SELECT r.lote::text AS lote, MIN(r.data) AS data, MIN(v.cliente) AS cliente,
               SUM(r.valor) AS valor, MIN(r.forma) AS forma,
               string_agg('#' || r.venda_id::text, ', ' ORDER BY r.venda_id) AS vendas,
               MIN(r.observacao) AS observacao, MIN(r.criado_por) AS criado_por,
               MAX(r.criado_em) AS criado_em
        FROM venda_recebimento r
        JOIN venda v ON v.id = r.venda_id
        GROUP BY r.lote
        ORDER BY MIN(r.data) DESC, MAX(r.criado_em) DESC
        LIMIT %s
        """,
        (limite,),
    )


def recebido_no_periodo(inicio, fim):
    return run_query(
        "SELECT COALESCE(SUM(valor), 0) AS total FROM venda_recebimento WHERE data BETWEEN %s AND %s",
        (inicio, fim),
    )[0]["total"]


def excluir_recebimento(lote):
    """Apaga um pagamento registrado por engano: o valor volta a ser devido."""
    feito = run_query("DELETE FROM venda_recebimento WHERE lote = %s::uuid RETURNING id", (lote,))
    return len(feito)


# ----------------------------------------------------------------
# Entregas das vendas online
# ----------------------------------------------------------------
def vendas_online(entregue, limite=200):
    return run_query(
        """
        SELECT v.id, v.data_venda::date AS data, v.cliente, v.valor_total, v.forma_pagamento, v.data_entrega,
               string_agg('#' || p.id::text || ' ' || p.descricao, ', ' ORDER BY p.id) AS pecas
        FROM venda v
        LEFT JOIN item_venda iv ON iv.venda_id = v.id
        LEFT JOIN produto p ON p.id = iv.produto_id
        WHERE v.canal = 'online' AND v.entregue = %s
        GROUP BY v.id
        ORDER BY CASE WHEN %s THEN v.data_entrega END DESC NULLS LAST, v.data_venda, v.id
        LIMIT %s
        """,
        (entregue, entregue, limite),
    )


def marcar_entregues(venda_ids, data_entrega):
    feito = run_query(
        "UPDATE venda SET entregue = true, data_entrega = %s "
        "WHERE id = ANY(%s) AND canal = 'online' AND entregue = false RETURNING id",
        (data_entrega, list(venda_ids)),
    )
    return len(feito)


def desfazer_entrega(venda_id):
    feito = run_query(
        "UPDATE venda SET entregue = false, data_entrega = NULL "
        "WHERE id = %s AND canal = 'online' AND entregue = true RETURNING id",
        (venda_id,),
    )
    return len(feito)
