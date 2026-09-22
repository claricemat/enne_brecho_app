"""Importação de extrato OFX e conciliação bancária pelo plano de contas.

Cada lançamento do extrato (extrato_lancamento) fica "pendente" até receber uma
conta analítica do plano de contas; aí ele passa a "conciliado".
"""

from db import run_query, transacao

TAMANHO_LOTE = 500


def normalizar_descricao(texto):
    """Chave para comparar descrições: sem espaços sobrando e sem diferença de caixa."""
    return " ".join(str(texto or "").split()).upper()


# ----------------------------------------------------------------
# Contas bancárias e importação
# ----------------------------------------------------------------
def contas_bancarias():
    return run_query(
        "SELECT id, nome, ofx_banco_id, ofx_conta_id FROM conta_financeira "
        "WHERE tipo = 'banco' ORDER BY nome"
    )


def fitids_ja_importados(conta_id, fitids):
    if not fitids:
        return set()
    linhas = run_query(
        "SELECT fitid FROM extrato_lancamento WHERE conta_financeira_id = %s AND fitid = ANY(%s)",
        (conta_id, list(fitids)),
    )
    return {l["fitid"] for l in linhas}


def importar_lancamentos(conta_id, arquivo, ofx, usuario, vincular_conta=False):
    """Grava os lançamentos do OFX na conta. Reimportar o mesmo extrato (ou um
    extrato que se sobrepõe) não duplica: o que já existe é ignorado.

    Tudo numa transação. Retorna (novos, ignorados)."""
    lancamentos = ofx["lancamentos"]
    novos = 0
    with transacao() as executar:
        if vincular_conta:
            executar(
                "UPDATE conta_financeira SET ofx_banco_id = %s, ofx_conta_id = %s WHERE id = %s",
                (ofx["banco_id"], ofx["conta_id"], conta_id),
            )
        for i in range(0, len(lancamentos), TAMANHO_LOTE):
            lote = lancamentos[i : i + TAMANHO_LOTE]
            marcadores = ",".join(["(%s,%s,%s,%s,%s,%s,%s,%s)"] * len(lote))
            params = []
            for l in lote:
                params += [conta_id, l["fitid"], l["data"], l["valor"], l["descricao"] or None,
                           l["tipo"] or None, arquivo, usuario]
            inseridos = executar(
                "INSERT INTO extrato_lancamento "
                "(conta_financeira_id, fitid, data, valor, descricao, tipo_ofx, arquivo, importado_por) "
                f"VALUES {marcadores} ON CONFLICT (conta_financeira_id, fitid) DO NOTHING RETURNING id",
                params,
                fetch=True,
            )
            novos += len(inseridos)
    return novos, len(lancamentos) - novos


def cobertura_por_conta():
    """Primeira/última data importada e pendências de cada conta bancária."""
    return run_query(
        """
        SELECT cf.nome AS conta,
               MIN(e.data) AS primeiro_lancamento,
               MAX(e.data) AS ultimo_lancamento,
               COUNT(e.id) AS lancamentos,
               COUNT(e.id) FILTER (WHERE e.plano_conta_id IS NULL) AS pendentes
        FROM conta_financeira cf
        LEFT JOIN extrato_lancamento e ON e.conta_financeira_id = cf.id
        WHERE cf.tipo = 'banco'
        GROUP BY cf.id, cf.nome
        ORDER BY cf.nome
        """
    )


# ----------------------------------------------------------------
# Consulta do extrato
# ----------------------------------------------------------------
def ultima_data_extrato(conta_id=None):
    linha = run_query(
        "SELECT MAX(data) AS ultima FROM extrato_lancamento "
        "WHERE (%s::integer IS NULL OR conta_financeira_id = %s::integer)",
        (conta_id, conta_id),
    )[0]
    return linha["ultima"]


def resumo_periodo(conta_id, inicio, fim):
    return run_query(
        """
        SELECT COALESCE(SUM(valor) FILTER (WHERE valor > 0), 0) AS creditos,
               COALESCE(SUM(valor) FILTER (WHERE valor < 0), 0) AS debitos,
               COUNT(*) AS total,
               COUNT(*) FILTER (WHERE plano_conta_id IS NULL) AS pendentes_qtd,
               COALESCE(SUM(valor) FILTER (WHERE plano_conta_id IS NULL), 0) AS pendentes_valor
        FROM extrato_lancamento
        WHERE data BETWEEN %s AND %s
          AND (%s::integer IS NULL OR conta_financeira_id = %s::integer)
        """,
        (inicio, fim, conta_id, conta_id),
    )[0]


def carregar_extrato(conta_id, inicio, fim, situacao, limite=500):
    """situacao: 'pendentes', 'conciliados' ou 'todos'. Traz limite+1 linhas
    (a linha a mais serve só para saber se a lista foi cortada)."""
    filtro_situacao = {
        "pendentes": "AND e.plano_conta_id IS NULL",
        "conciliados": "AND e.plano_conta_id IS NOT NULL",
        "todos": "",
    }[situacao]
    return run_query(
        f"""
        SELECT e.id, e.data, cf.nome AS conta, e.descricao, e.valor,
               e.plano_conta_id, e.conciliado_em, e.conciliado_por
        FROM extrato_lancamento e
        JOIN conta_financeira cf ON cf.id = e.conta_financeira_id
        WHERE e.data BETWEEN %s AND %s
          AND (%s::integer IS NULL OR e.conta_financeira_id = %s::integer)
          {filtro_situacao}
        ORDER BY e.data, e.id
        LIMIT %s
        """,
        (inicio, fim, conta_id, conta_id, limite + 1),
    )


def sugestoes_por_descricao():
    """Para cada descrição já conciliada, a última conta do plano usada nela.
    Serve pra pré-preencher lançamentos repetidos (tarifa, aluguel...)."""
    linhas = run_query(
        """
        SELECT DISTINCT ON (upper(btrim(descricao))) upper(btrim(descricao)) AS chave, plano_conta_id
        FROM extrato_lancamento
        WHERE plano_conta_id IS NOT NULL AND descricao IS NOT NULL
        ORDER BY upper(btrim(descricao)), conciliado_em DESC
        """
    )
    return {l["chave"]: l["plano_conta_id"] for l in linhas}


def resumo_por_plano(conta_id, inicio, fim):
    return run_query(
        """
        SELECT pc.tipo, pc.subgrupo, pc.nome,
               COUNT(*) AS lancamentos,
               COALESCE(SUM(e.valor) FILTER (WHERE e.valor > 0), 0) AS creditos,
               COALESCE(SUM(e.valor) FILTER (WHERE e.valor < 0), 0) AS debitos,
               SUM(e.valor) AS liquido
        FROM extrato_lancamento e
        JOIN plano_contas pc ON pc.id = e.plano_conta_id
        WHERE e.data BETWEEN %s AND %s
          AND (%s::integer IS NULL OR e.conta_financeira_id = %s::integer)
        GROUP BY pc.tipo, pc.subgrupo, pc.nome
        """,
        (inicio, fim, conta_id, conta_id),
    )


# ----------------------------------------------------------------
# Conciliação
# ----------------------------------------------------------------
def conciliar(pares, usuario):
    """pares: lista de (lancamento_id, plano_conta_id). Só concilia o que ainda
    está pendente (se a outra sócia conciliou antes, aquele lançamento é pulado).
    Uma única instrução, dentro de transação. Retorna quantos foram conciliados."""
    if not pares:
        return 0
    marcadores = ",".join(["(%s::integer, %s::integer)"] * len(pares))
    params = [usuario]
    for lancamento_id, plano_conta_id in pares:
        params += [lancamento_id, plano_conta_id]
    with transacao() as executar:
        feito = executar(
            f"""
            UPDATE extrato_lancamento e
            SET plano_conta_id = v.plano_conta_id, conciliado_em = now(), conciliado_por = %s
            FROM (VALUES {marcadores}) AS v(id, plano_conta_id)
            WHERE e.id = v.id AND e.plano_conta_id IS NULL
            RETURNING e.id
            """,
            params,
            fetch=True,
        )
    return len(feito)


def desfazer_conciliacao(ids):
    with transacao() as executar:
        feito = executar(
            """
            UPDATE extrato_lancamento
            SET plano_conta_id = NULL, conciliado_em = NULL, conciliado_por = NULL
            WHERE id = ANY(%s) AND plano_conta_id IS NOT NULL
            RETURNING id
            """,
            (list(ids),),
            fetch=True,
        )
    return len(feito)
