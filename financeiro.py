"""Financeiro: extrato OFX, conciliação com o sistema, caixa e balancete.

Tudo o que entra e sai de cada conta/caixa e foi registrado no sistema aparece
na view movimento_financeiro (vendas por forma de pagamento, recebimentos do
fiado, despesas pagas, pagamentos de compras de peças, reembolsos de devolução
e lançamentos manuais). Valor > 0 = entrada; valor < 0 = saída.

Um lançamento do extrato bancário (extrato_lancamento) fica "pendente" até ser
conciliado de um destes jeitos:
  - com um ou mais movimentos do sistema (conciliacao_vinculo) — a conta do
    plano vem do próprio movimento (venda -> Venda de peças, despesa -> a conta dela...);
  - direto numa conta do plano de contas (tarifa bancária, rendimento...), para o
    que não foi registrado em nenhuma outra tela.
"""

import re
import uuid
from datetime import timedelta
from decimal import Decimal

from db import run_query, transacao

TAMANHO_LOTE = 500
CENTAVO = Decimal("0.01")

# formas de pagamento que movimentam dinheiro numa conta ou no caixa
FORMAS_COM_CONTA = ["Dinheiro", "Pix", "Cartão de Crédito", "Cartão de Débito", "Outro"]
# conta obrigatória (em "Outro" a conta é opcional)
FORMAS_CONTA_OBRIGATORIA = ["Dinheiro", "Pix", "Cartão de Crédito", "Cartão de Débito"]
# reembolso de devolução que tira dinheiro de uma conta/caixa (o estorno no cartão
# não aparece como saída separada no banco: a maquininha desconta do repasse)
FORMA_PADRAO_REEMBOLSO = {
    "Dinheiro": "Dinheiro",
    "Pix": "Pix",
    "Outro": "Outro",
}

ORIGENS = {
    "venda": "Venda",
    "fiado": "Recebimento do fiado",
    "despesa": "Despesa",
    "fornecedora": "Pagamento de compra",
    "devolucao": "Devolução",
    "manual": "Lançamento manual",
}

# janela de datas para casar extrato x sistema (dias do extrato em relação ao movimento)
JANELA_PADRAO = (-3, 5)      # o banco pode lançar até 5 dias depois (ou 3 antes) da data registrada
JANELA_CARTAO = (0, 35)      # venda no cartão cai na conta de 0 a 35 dias depois


class ConciliacaoInvalidaError(Exception):
    """A conciliação não pode ser gravada (valores não batem, lançamento já conciliado...)."""


class LancamentoInvalidoError(Exception):
    """Lançamento manual inválido (ou já conciliado, ao tentar excluir)."""


def _dec(valor):
    return Decimal(str(valor if valor is not None else 0)).quantize(CENTAVO)


def _posicional(sql, valores):
    """run_query só aceita parâmetros posicionais: troca %(nome)s por %s, na ordem."""
    nomes = re.findall(r"%\((\w+)\)s", sql)
    return re.sub(r"%\(\w+\)s", "%s", sql), tuple(valores[n] for n in nomes)


def normalizar_descricao(texto):
    """Chave para comparar descrições: sem espaços sobrando e sem diferença de caixa."""
    return " ".join(str(texto or "").split()).upper()


# ----------------------------------------------------------------
# Contas e configurações
# ----------------------------------------------------------------
def contas_bancarias():
    return run_query(
        "SELECT id, nome, ofx_banco_id, ofx_conta_id FROM conta_financeira "
        "WHERE tipo = 'banco' ORDER BY nome"
    )


def contas_financeiras():
    """Todas as contas (bancos e caixa), com o saldo inicial."""
    return run_query(
        "SELECT id, nome, tipo, saldo_inicial, saldo_inicial_data FROM conta_financeira ORDER BY tipo, nome"
    )


def rotulo_conta_financeira(conta):
    return f"{conta['nome']} ({'caixa' if conta['tipo'] == 'caixa' else 'conta bancária'})"


def contas_padrao():
    """{forma de pagamento: conta_financeira_id}"""
    return {l["forma"]: l["conta_financeira_id"] for l in run_query("SELECT forma, conta_financeira_id FROM forma_pagamento_conta")}


def salvar_contas_padrao(padroes):
    """padroes: {forma: conta_id ou None}."""
    with transacao() as executar:
        for forma, conta_id in padroes.items():
            if conta_id is None:
                executar("DELETE FROM forma_pagamento_conta WHERE forma = %s", (forma,))
            else:
                executar(
                    "INSERT INTO forma_pagamento_conta (forma, conta_financeira_id) VALUES (%s, %s) "
                    "ON CONFLICT (forma) DO UPDATE SET conta_financeira_id = EXCLUDED.conta_financeira_id",
                    (forma, conta_id),
                )


def salvar_saldos_iniciais(saldos):
    """saldos: [(conta_id, valor, data)]."""
    with transacao() as executar:
        for conta_id, valor, data in saldos:
            executar(
                "UPDATE conta_financeira SET saldo_inicial = %s, saldo_inicial_data = %s WHERE id = %s",
                (_dec(valor), data, conta_id),
            )


def config_planos():
    """{'pagamento_compra': plano_conta_id, 'transferencia': plano_conta_id}"""
    return {l["chave"]: l["plano_conta_id"] for l in run_query("SELECT chave, plano_conta_id FROM config_financeiro")}


def salvar_config_planos(config):
    with transacao() as executar:
        for chave, plano_id in config.items():
            executar(
                "INSERT INTO config_financeiro (chave, plano_conta_id) VALUES (%s, %s) "
                "ON CONFLICT (chave) DO UPDATE SET plano_conta_id = EXCLUDED.plano_conta_id",
                (chave, plano_id),
            )


# ----------------------------------------------------------------
# Lançamentos registrados sem conta (antes da integração)
# ----------------------------------------------------------------
_SQL_SEM_CONTA = """
    SELECT 'venda' AS origem, vp.id, (v.data_venda AT TIME ZONE 'America/Sao_Paulo')::date AS data,
           'Venda #' || v.id || COALESCE(' — ' || v.cliente, '') AS descricao, vp.forma, vp.valor AS valor
    FROM venda_pagamento vp JOIN venda v ON v.id = vp.venda_id
    WHERE vp.conta_financeira_id IS NULL AND vp.valor > 0 AND vp.forma = ANY(%(formas)s)
      AND (v.data_venda AT TIME ZONE 'America/Sao_Paulo')::date BETWEEN %(inicio)s AND %(fim)s
    UNION ALL
    SELECT 'fiado', r.id, r.data, 'Recebimento fiado — venda #' || v.id || COALESCE(' — ' || v.cliente, ''),
           r.forma, r.valor
    FROM venda_recebimento r JOIN venda v ON v.id = r.venda_id
    WHERE r.conta_financeira_id IS NULL AND r.forma = ANY(%(formas)s) AND r.data BETWEEN %(inicio)s AND %(fim)s
    UNION ALL
    SELECT 'despesa', d.id, COALESCE(d.data_pagamento, d.data),
           'Despesa — ' || pc.nome || COALESCE(' — ' || d.descricao, ''), NULL, -d.valor
    FROM despesa d JOIN plano_contas pc ON pc.id = d.plano_conta_id
    WHERE d.status_pagamento = 'pago' AND d.conta_financeira_id IS NULL
      AND COALESCE(d.data_pagamento, d.data) BETWEEN %(inicio)s AND %(fim)s
    UNION ALL
    SELECT 'devolucao', d.id, d.data, 'Devolução — venda #' || d.venda_id, d.forma_reembolso, -d.valor_devolvido
    FROM devolucao d
    WHERE d.conta_financeira_id IS NULL AND d.valor_devolvido > 0
      AND d.forma_reembolso = ANY(%(formas_reembolso)s) AND d.data BETWEEN %(inicio)s AND %(fim)s
"""


def lancamentos_sem_conta(inicio, fim):
    return run_query(*_posicional(
        "SELECT * FROM (" + _SQL_SEM_CONTA + ") x ORDER BY data, origem, id",
        {"formas": FORMAS_COM_CONTA, "formas_reembolso": list(FORMA_PADRAO_REEMBOLSO),
         "inicio": inicio, "fim": fim},
    ))


def definir_contas(alteracoes):
    """alteracoes: [(origem, id, conta_id)] — só para lançamentos ainda sem conta."""
    feitos = 0
    with transacao() as executar:
        for origem, item_id, conta_id in alteracoes:
            if origem == "venda":
                sql = "UPDATE venda_pagamento SET conta_financeira_id = %s WHERE id = %s AND conta_financeira_id IS NULL RETURNING id"
            elif origem == "fiado":
                sql = "UPDATE venda_recebimento SET conta_financeira_id = %s WHERE id = %s AND conta_financeira_id IS NULL RETURNING id"
            elif origem == "despesa":
                sql = ("UPDATE despesa SET conta_financeira_id = %s, data_pagamento = COALESCE(data_pagamento, data) "
                       "WHERE id = %s AND status_pagamento = 'pago' AND conta_financeira_id IS NULL RETURNING id")
            elif origem == "devolucao":
                sql = "UPDATE devolucao SET conta_financeira_id = %s WHERE id = %s AND conta_financeira_id IS NULL RETURNING id"
            else:
                continue
            feitos += len(executar(sql, (conta_id, item_id), fetch=True))
    return feitos


def aplicar_contas_padrao(inicio, fim):
    """Preenche a conta das vendas e recebimentos do fiado sem conta, pela conta padrão de cada forma."""
    with transacao() as executar:
        vendas = executar(
            """
            UPDATE venda_pagamento vp SET conta_financeira_id = fpc.conta_financeira_id
            FROM forma_pagamento_conta fpc, venda v
            WHERE fpc.forma = vp.forma AND v.id = vp.venda_id AND vp.conta_financeira_id IS NULL
              AND (v.data_venda AT TIME ZONE 'America/Sao_Paulo')::date BETWEEN %s AND %s
            RETURNING vp.id
            """,
            (inicio, fim),
            fetch=True,
        )
        fiado = executar(
            """
            UPDATE venda_recebimento r SET conta_financeira_id = fpc.conta_financeira_id
            FROM forma_pagamento_conta fpc
            WHERE fpc.forma = r.forma AND r.conta_financeira_id IS NULL AND r.data BETWEEN %s AND %s
            RETURNING r.id
            """,
            (inicio, fim),
            fetch=True,
        )
    return len(vendas) + len(fiado)


# ----------------------------------------------------------------
# Importação do extrato
# ----------------------------------------------------------------
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
               COUNT(e.id) FILTER (WHERE e.conciliado_em IS NULL) AS pendentes
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
               COUNT(*) FILTER (WHERE conciliado_em IS NULL) AS pendentes_qtd,
               COALESCE(SUM(valor) FILTER (WHERE conciliado_em IS NULL), 0) AS pendentes_valor
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
        "pendentes": "AND e.conciliado_em IS NULL",
        "conciliados": "AND e.conciliado_em IS NOT NULL",
        "todos": "",
    }[situacao]
    return run_query(
        f"""
        SELECT e.id, e.data, e.conta_financeira_id, cf.nome AS conta, e.descricao, e.valor,
               e.plano_conta_id, e.conciliado_em, e.conciliado_por,
               (SELECT string_agg(m.descricao, ' + ' ORDER BY m.data, m.origem_id)
                  FROM conciliacao_vinculo c
                  JOIN movimento_financeiro m ON m.origem = c.origem AND m.origem_id = c.origem_id
                 WHERE c.extrato_lancamento_id = e.id) AS vinculos
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
    """Para cada descrição já classificada direto no plano, a última conta usada nela.
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


# ----------------------------------------------------------------
# Movimentos do sistema ainda não conciliados
# ----------------------------------------------------------------
_SQL_MOV_LIVRES = """
    SELECT m.origem, m.origem_id, m.conta_financeira_id, m.data, m.valor, m.plano_conta_id,
           m.descricao, m.forma
    FROM movimento_financeiro m
    WHERE m.conta_financeira_id = ANY(%s)
      AND m.data BETWEEN %s AND %s
      AND NOT EXISTS (SELECT 1 FROM conciliacao_vinculo c
                      WHERE c.origem = m.origem AND c.origem_id = m.origem_id)
"""


def movimentos_livres(conta_ids, inicio, fim):
    if not conta_ids:
        return []
    return run_query(_SQL_MOV_LIVRES + " ORDER BY m.data, m.origem, m.origem_id", (list(conta_ids), inicio, fim))


def _janela(mov):
    forma = (mov.get("forma") or "")
    return JANELA_CARTAO if mov["origem"] in ("venda", "fiado") and forma.startswith("Cartão") else JANELA_PADRAO


JANELA_POSSIVEL = 20   # dias, para "possível par" (data mais longe ou outra conta)


def _parear(candidatos):
    """candidatos: [(prioridade, extrato_id, mov)]. Cada extrato e cada movimento
    entram em um par só; menor prioridade primeiro."""
    candidatos.sort(key=lambda p: (p[0], p[1], p[2]["origem"], p[2]["origem_id"]))
    usados_extrato, usados_mov, resultado = set(), set(), {}
    for _, extrato_id, m in candidatos:
        chave = (m["origem"], m["origem_id"])
        if extrato_id in usados_extrato or chave in usados_mov:
            continue
        usados_extrato.add(extrato_id)
        usados_mov.add(chave)
        resultado[extrato_id] = m
    return resultado


def sugerir_vinculos(lancamentos, com_possiveis=False):
    """Casa cada lançamento pendente do extrato com um movimento do sistema de
    mesmo valor. Cada movimento só é usado uma vez.

    - encontrados: mesma conta e data próxima (janela padrão / cartão) — confiáveis;
    - possíveis (com_possiveis=True): mesmo valor, mas a data está mais longe (até
      JANELA_POSSIVEL dias) ou a conta registrada é outra — para conferir.
    Retorna {extrato_id: movimento}, ou ({...encontrados}, {...possíveis})."""
    if not lancamentos:
        return ({}, {}) if com_possiveis else {}
    inicio = min(l["data"] for l in lancamentos) - timedelta(days=max(JANELA_CARTAO[1], JANELA_POSSIVEL))
    fim = max(l["data"] for l in lancamentos) + timedelta(days=JANELA_POSSIVEL)
    # possíveis em outra conta: só contas bancárias (o que foi registrado no caixa não passa pelo banco)
    contas = ({c["id"] for c in contas_bancarias()} | {l["conta_financeira_id"] for l in lancamentos}
              if com_possiveis else {l["conta_financeira_id"] for l in lancamentos})
    por_valor = {}
    for m in movimentos_livres(contas, inicio, fim):
        por_valor.setdefault(_dec(m["valor"]), []).append(m)

    fortes, fracos = [], []
    for l in lancamentos:
        for m in por_valor.get(_dec(l["valor"]), []):
            dias = (l["data"] - m["data"]).days
            menor, maior = _janela(m)
            mesma_conta = m["conta_financeira_id"] == l["conta_financeira_id"]
            na_janela = menor <= dias <= maior
            if mesma_conta and na_janela:
                fortes.append((abs(dias), l["id"], m))
            elif (mesma_conta and abs(dias) <= JANELA_POSSIVEL) or (not mesma_conta and na_janela):
                # mesma conta com a data mais longe, ou outra conta com a data próxima
                fracos.append(((0 if mesma_conta else 100) + abs(dias), l["id"], m))
    encontrados = _parear(fortes)
    if not com_possiveis:
        return encontrados
    usados = {(m["origem"], m["origem_id"]) for m in encontrados.values()}
    possiveis = _parear([f for f in fracos if f[1] not in encontrados
                         and (f[2]["origem"], f[2]["origem_id"]) not in usados])
    return encontrados, possiveis


def pares_no_extrato(movimentos):
    """Para movimentos do sistema ainda soltos: o lançamento do extrato de mesmo
    valor mais provável (mesma conta primeiro, depois a data mais próxima, até
    JANELA_POSSIVEL dias), que ainda não esteja ligado a outro movimento do sistema.
    Inclui os já conciliados direto no plano — é o caso de lançamento contado em dobro.
    Retorna {(origem, origem_id): lançamento do extrato}."""
    if not movimentos:
        return {}
    inicio = min(m["data"] for m in movimentos) - timedelta(days=JANELA_POSSIVEL)
    fim = max(m["data"] for m in movimentos) + timedelta(days=max(JANELA_POSSIVEL, JANELA_CARTAO[1]))
    linhas = run_query(
        """
        SELECT e.id, e.conta_financeira_id, cf.nome AS conta, e.data, e.valor, e.descricao,
               e.plano_conta_id, e.conciliado_em
        FROM extrato_lancamento e
        JOIN conta_financeira cf ON cf.id = e.conta_financeira_id
        WHERE e.data BETWEEN %s AND %s
          AND NOT EXISTS (SELECT 1 FROM conciliacao_vinculo c WHERE c.extrato_lancamento_id = e.id)
        """,
        (inicio, fim),
    )
    por_valor = {}
    for l in linhas:
        por_valor.setdefault(_dec(l["valor"]), []).append(l)
    candidatos = []
    for m in movimentos:
        for l in por_valor.get(_dec(m["valor"]), []):
            dias = (l["data"] - m["data"]).days
            menor, maior = _janela(m)
            mesma_conta = l["conta_financeira_id"] == m["conta_financeira_id"]
            na_janela = menor <= dias <= maior
            if not ((mesma_conta and (na_janela or abs(dias) <= JANELA_POSSIVEL)) or (not mesma_conta and na_janela)):
                continue
            prioridade = (0 if mesma_conta else 100) + abs(dias)
            candidatos.append((prioridade, m, l))
    candidatos.sort(key=lambda c: (c[0], c[2]["id"]))
    usados_ext, resultado = set(), {}
    for _, m, l in candidatos:
        chave = (m["origem"], m["origem_id"])
        if chave in resultado or l["id"] in usados_ext:
            continue
        usados_ext.add(l["id"])
        resultado[chave] = l
    return resultado


def movimentos_sem_extrato(conta_id, inicio, fim):
    """Movimentos do sistema nas contas bancárias, no período, que ainda não
    apareceram em nenhum extrato conciliado."""
    contas = [conta_id] if conta_id else [c["id"] for c in contas_bancarias()]
    return movimentos_livres(contas, inicio, fim)


# ----------------------------------------------------------------
# Conciliação
# ----------------------------------------------------------------
_SQL_CONTA_DO_MOVIMENTO = {
    "venda": "UPDATE venda_pagamento SET conta_financeira_id = %s WHERE id = %s",
    "fiado": "UPDATE venda_recebimento SET conta_financeira_id = %s WHERE id = %s",
    "despesa": "UPDATE despesa SET conta_financeira_id = %s WHERE id = %s",
    "fornecedora": "UPDATE baixa_compra_forma SET conta_financeira_id = %s WHERE id = %s AND forma = 'conta'",
    "devolucao": "UPDATE devolucao SET conta_financeira_id = %s WHERE id = %s",
    "manual": "UPDATE movimento_manual SET conta_financeira_id = %s WHERE id = %s",
}


def _gravar_vinculos(executar, extrato_id, movimentos, usuario):
    """Liga o lançamento do extrato aos movimentos. O extrato é a verdade: se o
    movimento foi registrado em outra conta, a conta dele é corrigida para a do extrato."""
    conta_extrato = executar(
        "SELECT conta_financeira_id FROM extrato_lancamento WHERE id = %s", (extrato_id,), fetch=True
    )[0]["conta_financeira_id"]
    for origem, origem_id, valor in movimentos:
        executar(_SQL_CONTA_DO_MOVIMENTO[origem] + " AND conta_financeira_id IS DISTINCT FROM %s",
                 (conta_extrato, origem_id, conta_extrato))
        executar(
            "INSERT INTO conciliacao_vinculo (extrato_lancamento_id, origem, origem_id, valor) VALUES (%s, %s, %s, %s)",
            (extrato_id, origem, origem_id, _dec(valor)),
        )
    executar(
        "UPDATE extrato_lancamento SET plano_conta_id = NULL, conciliado_em = now(), conciliado_por = %s WHERE id = %s",
        (usuario, extrato_id),
    )


def conciliar_lote(itens, usuario):
    """itens: dicts com extrato_id e
         - movimentos=[(origem, origem_id, valor)]  -> conciliado com o sistema, ou
         - plano_conta_id=...                       -> classificado direto no plano.
    Lançamentos já conciliados (pela outra pessoa) ou movimentos já usados são pulados.
    Tudo numa transação. Retorna (conciliados, pulados)."""
    if not itens:
        return 0, 0
    feitos = pulados = 0
    with transacao() as executar:
        livres = {
            r["id"]
            for r in executar(
                "SELECT id FROM extrato_lancamento WHERE id = ANY(%s) AND conciliado_em IS NULL FOR UPDATE",
                ([i["extrato_id"] for i in itens],),
                fetch=True,
            )
        }
        pares = [(o, oid) for i in itens for (o, oid, _) in i.get("movimentos") or []]
        usados = set()
        if pares:
            usados = {
                (r["origem"], r["origem_id"])
                for r in executar(
                    "SELECT origem, origem_id FROM conciliacao_vinculo "
                    "WHERE (origem, origem_id) IN (SELECT * FROM unnest(%s::text[], %s::int[]))",
                    ([p[0] for p in pares], [p[1] for p in pares]),
                    fetch=True,
                )
            }
        for item in itens:
            if item["extrato_id"] not in livres:
                pulados += 1
                continue
            movimentos = item.get("movimentos")
            if movimentos:
                if any((o, oid) in usados for o, oid, _ in movimentos):
                    pulados += 1
                    continue
                _gravar_vinculos(executar, item["extrato_id"], movimentos, usuario)
                usados.update((o, oid) for o, oid, _ in movimentos)
            else:
                executar(
                    "UPDATE extrato_lancamento SET plano_conta_id = %s, conciliado_em = now(), conciliado_por = %s "
                    "WHERE id = %s",
                    (item["plano_conta_id"], usuario, item["extrato_id"]),
                )
            feitos += 1
    return feitos, pulados


def ligar_ao_extrato(pares, usuario):
    """pares: [(extrato_id, origem, origem_id, valor)]. Liga cada movimento solto do
    sistema ao lançamento do extrato — inclusive os que tinham sido conciliados direto
    numa conta do plano (a classificação direta é trocada pela ligação, o que tira a
    duplicidade do balancete). Retorna (ligados, pulados)."""
    ligados = pulados = 0
    with transacao() as executar:
        for extrato_id, origem, origem_id, valor in pares:
            extrato = executar(
                "SELECT id FROM extrato_lancamento e WHERE id = %s AND NOT EXISTS "
                "(SELECT 1 FROM conciliacao_vinculo c WHERE c.extrato_lancamento_id = e.id) FOR UPDATE",
                (extrato_id,), fetch=True,
            )
            ja = executar("SELECT 1 FROM conciliacao_vinculo WHERE origem = %s AND origem_id = %s",
                          (origem, origem_id), fetch=True)
            if not extrato or ja:
                pulados += 1
                continue
            _gravar_vinculos(executar, extrato_id, [(origem, origem_id, valor)], usuario)
            ligados += 1
    return ligados, pulados


def conciliar_com_diferenca(extrato_id, movimentos, plano_ajuste_id, usuario):
    """Concilia um lançamento do extrato com vários movimentos do sistema. Se a soma
    não bater (ex.: taxa da maquininha), a diferença vira um lançamento de ajuste na
    conta do plano escolhida. movimentos: [(origem, origem_id, valor)]."""
    if not movimentos:
        raise ConciliacaoInvalidaError("Escolha ao menos um lançamento do sistema.")
    with transacao() as executar:
        linha = executar(
            "SELECT id, conta_financeira_id, data, valor, descricao FROM extrato_lancamento "
            "WHERE id = %s AND conciliado_em IS NULL FOR UPDATE",
            (extrato_id,),
            fetch=True,
        )
        if not linha:
            raise ConciliacaoInvalidaError("Esse lançamento já foi conciliado (talvez pela outra pessoa). Atualize a página.")
        e = linha[0]
        ja = executar(
            "SELECT origem, origem_id FROM conciliacao_vinculo "
            "WHERE (origem, origem_id) IN (SELECT * FROM unnest(%s::text[], %s::int[]))",
            ([m[0] for m in movimentos], [m[1] for m in movimentos]),
            fetch=True,
        )
        if ja:
            raise ConciliacaoInvalidaError("Algum lançamento do sistema escolhido já foi conciliado. Atualize a página.")
        diferenca = _dec(e["valor"]) - sum((_dec(v) for _, _, v in movimentos), Decimal("0"))
        movimentos = list(movimentos)
        if diferenca != 0:
            if not plano_ajuste_id:
                raise ConciliacaoInvalidaError("Escolha a conta do plano para lançar a diferença.")
            ajuste_id = executar(
                """
                INSERT INTO movimento_manual (conta_financeira_id, data, valor, plano_conta_id, descricao,
                                              extrato_lancamento_id, criado_por)
                VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id
                """,
                (e["conta_financeira_id"], e["data"], diferenca, plano_ajuste_id,
                 f"Diferença da conciliação — {e['descricao'] or 'extrato'}", extrato_id, usuario),
                fetch=True,
            )[0]["id"]
            movimentos.append(("manual", ajuste_id, diferenca))
        _gravar_vinculos(executar, extrato_id, movimentos, usuario)
    return diferenca


def desfazer_conciliacao(ids):
    """Os lançamentos voltam a pendentes; ajustes de diferença criados na conciliação são apagados."""
    ids = list(ids)
    with transacao() as executar:
        executar("DELETE FROM conciliacao_vinculo WHERE extrato_lancamento_id = ANY(%s)", (ids,))
        executar("DELETE FROM movimento_manual WHERE extrato_lancamento_id = ANY(%s)", (ids,))
        feito = executar(
            """
            UPDATE extrato_lancamento
            SET plano_conta_id = NULL, conciliado_em = NULL, conciliado_por = NULL
            WHERE id = ANY(%s) AND conciliado_em IS NOT NULL
            RETURNING id
            """,
            (ids,),
            fetch=True,
        )
    return len(feito)


def limpar_vinculos_orfaos():
    """Se um movimento conciliado deixou de existir (pagamento do fiado excluído,
    despesa desfeita...), o vínculo é apagado e o lançamento do extrato volta a pendente."""
    with transacao() as executar:
        afetados = executar(
            """
            DELETE FROM conciliacao_vinculo c
            WHERE NOT EXISTS (SELECT 1 FROM movimento_financeiro m
                              WHERE m.origem = c.origem AND m.origem_id = c.origem_id)
            RETURNING c.extrato_lancamento_id
            """,
            fetch=True,
        )
        if not afetados:
            return 0
        ids = list({a["extrato_lancamento_id"] for a in afetados})
        executar("DELETE FROM conciliacao_vinculo WHERE extrato_lancamento_id = ANY(%s)", (ids,))
        executar("DELETE FROM movimento_manual WHERE extrato_lancamento_id = ANY(%s)", (ids,))
        executar(
            "UPDATE extrato_lancamento SET conciliado_em = NULL, conciliado_por = NULL "
            "WHERE id = ANY(%s) AND plano_conta_id IS NULL",
            (ids,),
        )
    return len(ids)


# ----------------------------------------------------------------
# Lançamentos manuais (caixa e transferências)
# ----------------------------------------------------------------
def registrar_manual(conta_id, data, valor, plano_conta_id, descricao, usuario):
    """valor > 0 = entrada; valor < 0 = saída."""
    valor = _dec(valor)
    if valor == 0:
        raise LancamentoInvalidoError("Informe um valor maior que zero.")
    if not plano_conta_id:
        raise LancamentoInvalidoError("Escolha a conta do plano de contas.")
    return run_query(
        "INSERT INTO movimento_manual (conta_financeira_id, data, valor, plano_conta_id, descricao, criado_por) "
        "VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
        (conta_id, data, valor, plano_conta_id, descricao or None, usuario),
    )[0]["id"]


def registrar_transferencia(origem_id, destino_id, data, valor, descricao, usuario):
    """Ex.: depósito do dinheiro do caixa no banco. Sai de uma conta e entra na outra."""
    valor = _dec(valor)
    if valor <= 0:
        raise LancamentoInvalidoError("Informe um valor maior que zero.")
    if origem_id == destino_id:
        raise LancamentoInvalidoError("Escolha contas diferentes para a transferência.")
    codigo = str(uuid.uuid4())
    plano = config_planos().get("transferencia")
    with transacao() as executar:
        nomes = {r["id"]: r["nome"] for r in executar(
            "SELECT id, nome FROM conta_financeira WHERE id = ANY(%s)", ([origem_id, destino_id],), fetch=True)}
        texto = descricao or f"Transferência {nomes.get(origem_id, '')} → {nomes.get(destino_id, '')}"
        for conta_id, sinal in ((origem_id, -1), (destino_id, 1)):
            executar(
                "INSERT INTO movimento_manual (conta_financeira_id, data, valor, plano_conta_id, descricao, "
                "transferencia, criado_por) VALUES (%s, %s, %s, %s, %s, %s::uuid, %s)",
                (conta_id, data, valor * sinal, plano, texto, codigo, usuario),
            )
    return codigo


def excluir_manual(movimento_id):
    """Apaga um lançamento manual (as duas pontas, se for transferência).
    Não apaga o que já foi conciliado com o extrato."""
    with transacao() as executar:
        linha = executar(
            "SELECT id, transferencia, extrato_lancamento_id FROM movimento_manual WHERE id = %s FOR UPDATE",
            (movimento_id,),
            fetch=True,
        )
        if not linha:
            raise LancamentoInvalidoError("Lançamento não encontrado (talvez já excluído).")
        m = linha[0]
        if m["extrato_lancamento_id"]:
            raise LancamentoInvalidoError(
                "Esse é o ajuste de uma conciliação — para tirar, desfaça a conciliação no Financeiro."
            )
        if m["transferencia"]:
            ids = [r["id"] for r in executar(
                "SELECT id FROM movimento_manual WHERE transferencia = %s FOR UPDATE", (m["transferencia"],), fetch=True)]
        else:
            ids = [m["id"]]
        conciliados = executar(
            "SELECT 1 FROM conciliacao_vinculo WHERE origem = 'manual' AND origem_id = ANY(%s)", (ids,), fetch=True)
        if conciliados:
            raise LancamentoInvalidoError(
                "Esse lançamento já foi conciliado com o extrato do banco — desfaça a conciliação antes de excluir."
            )
        executar("DELETE FROM movimento_manual WHERE id = ANY(%s)", (ids,))
    return len(ids)


def movimentos_da_conta(conta_id, inicio, fim):
    """Extrato da conta pelo sistema: movimentos registrados + lançamentos do banco
    classificados direto no plano. Inclui se cada um está conciliado."""
    return run_query(
        """
        SELECT * FROM (
            SELECT m.origem, m.origem_id, m.data, m.valor, m.descricao, m.plano_conta_id,
                   EXISTS (SELECT 1 FROM conciliacao_vinculo c
                           WHERE c.origem = m.origem AND c.origem_id = m.origem_id) AS conciliado,
                   mm.transferencia IS NOT NULL AS transferencia,
                   mm.extrato_lancamento_id IS NOT NULL AS ajuste,
                   mm.criado_por
            FROM movimento_financeiro m
            LEFT JOIN movimento_manual mm ON m.origem = 'manual' AND mm.id = m.origem_id
            WHERE m.conta_financeira_id = %s AND m.data BETWEEN %s AND %s
            UNION ALL
            SELECT 'extrato', e.id, e.data, e.valor, COALESCE(e.descricao, 'Extrato'), e.plano_conta_id,
                   true, false, false, e.conciliado_por
            FROM extrato_lancamento e
            WHERE e.conta_financeira_id = %s AND e.plano_conta_id IS NOT NULL AND e.data BETWEEN %s AND %s
        ) x
        ORDER BY data, origem, origem_id
        """,
        (conta_id, inicio, fim, conta_id, inicio, fim),
    )


# ----------------------------------------------------------------
# Saldos e balancete
# ----------------------------------------------------------------
# razão do sistema: movimentos registrados + lançamentos do banco classificados direto no plano
_SQL_RAZAO = """
    SELECT conta_financeira_id, data, valor, plano_conta_id FROM movimento_financeiro
    UNION ALL
    SELECT conta_financeira_id, data, valor, plano_conta_id FROM extrato_lancamento
    WHERE plano_conta_id IS NOT NULL
"""


def saldos_contas(inicio, fim):
    """Para cada conta/caixa: saldo no início do período, entradas, saídas e saldo
    no fim, pelo sistema. Para as contas bancárias, também o saldo pelo extrato
    importado. Movimentos antes da data do saldo inicial são ignorados."""
    return run_query(*_posicional(
        """
        SELECT cf.id, cf.nome, cf.tipo, cf.saldo_inicial, cf.saldo_inicial_data,
               cf.saldo_inicial + COALESCE(SUM(r.valor) FILTER (WHERE r.data < %(inicio)s), 0) AS saldo_anterior,
               COALESCE(SUM(r.valor) FILTER (WHERE r.data BETWEEN %(inicio)s AND %(fim)s AND r.valor > 0), 0) AS entradas,
               COALESCE(SUM(r.valor) FILTER (WHERE r.data BETWEEN %(inicio)s AND %(fim)s AND r.valor < 0), 0) AS saidas,
               cf.saldo_inicial + COALESCE(SUM(r.valor) FILTER (WHERE r.data <= %(fim)s), 0) AS saldo_final,
               CASE WHEN cf.tipo = 'banco' THEN
                   cf.saldo_inicial + COALESCE((
                       SELECT SUM(e.valor) FROM extrato_lancamento e
                       WHERE e.conta_financeira_id = cf.id AND e.data <= %(fim)s
                         AND (cf.saldo_inicial_data IS NULL OR e.data >= cf.saldo_inicial_data)), 0)
               END AS saldo_extrato
        FROM conta_financeira cf
        LEFT JOIN (""" + _SQL_RAZAO + """) r
               ON r.conta_financeira_id = cf.id
              AND (cf.saldo_inicial_data IS NULL OR r.data >= cf.saldo_inicial_data)
        GROUP BY cf.id
        ORDER BY cf.tipo DESC, cf.nome
        """,
        {"inicio": inicio, "fim": fim},
    ))


def balancete(inicio, fim):
    """Entradas e saídas do período por conta do plano (regime de caixa)."""
    return run_query(
        """
        SELECT pc.id, pc.tipo, pc.subgrupo, pc.nome,
               COALESCE(SUM(r.valor) FILTER (WHERE r.valor > 0), 0) AS entradas,
               COALESCE(SUM(r.valor) FILTER (WHERE r.valor < 0), 0) AS saidas,
               COALESCE(SUM(r.valor), 0) AS saldo,
               COUNT(*) AS lancamentos
        FROM (""" + _SQL_RAZAO + """) r
        LEFT JOIN plano_contas pc ON pc.id = r.plano_conta_id
        WHERE r.data BETWEEN %s AND %s
        GROUP BY pc.id, pc.tipo, pc.subgrupo, pc.nome
        """,
        (inicio, fim),
    )


def pendencias_balancete(inicio, fim):
    """Quantos lançamentos do extrato no período ainda não foram conciliados
    (ficam fora do balancete) e quantos lançamentos do sistema não têm conta."""
    extrato = run_query(
        "SELECT COUNT(*) AS qtd, COALESCE(SUM(valor), 0) AS valor FROM extrato_lancamento "
        "WHERE conciliado_em IS NULL AND data BETWEEN %s AND %s",
        (inicio, fim),
    )[0]
    sem_conta = run_query(*_posicional(
        "SELECT COUNT(*) AS qtd FROM (" + _SQL_SEM_CONTA + ") x",
        {"formas": FORMAS_COM_CONTA, "formas_reembolso": list(FORMA_PADRAO_REEMBOLSO),
         "inicio": inicio, "fim": fim},
    ))[0]["qtd"]
    return {"extrato_qtd": extrato["qtd"], "extrato_valor": extrato["valor"], "sem_conta": sem_conta}
