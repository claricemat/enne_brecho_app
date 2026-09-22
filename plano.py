"""Plano de contas em 3 níveis: Grupo > Subgrupo > Analítico.

Ex.: Receita > Receita operacional > Venda de peças

No banco: grupo = plano_contas.tipo, subgrupo = plano_contas.subgrupo,
analítico = plano_contas.nome.
"""

from db import run_query

# valor no banco -> como aparece na tela (a ordem daqui é a ordem das listas)
GRUPOS = {
    "receita": "Receita",
    "custo": "Custo",
    "despesa": "Despesa",
    "transferencia": "Transferência",
}


def limpar_texto(texto):
    """Tira espaços sobrando: '  Receita   operacional ' -> 'Receita operacional'."""
    return " ".join(str(texto or "").split())


def rotulo_conta(conta):
    """'Receita › Receita operacional › Venda de peças'."""
    return f"{GRUPOS.get(conta['tipo'], conta['tipo'])} › {conta['subgrupo']} › {conta['nome']}"


def carregar_contas():
    """Todas as contas analíticas, na ordem grupo > subgrupo > nome."""
    contas = run_query("SELECT id, tipo, subgrupo, nome FROM plano_contas")
    ordem = list(GRUPOS)
    contas.sort(
        key=lambda c: (
            ordem.index(c["tipo"]) if c["tipo"] in ordem else len(ordem),
            c["subgrupo"].casefold(),
            c["nome"].casefold(),
        )
    )
    return contas


def subgrupos_do_grupo(contas, grupo):
    return sorted({c["subgrupo"] for c in contas if c["tipo"] == grupo}, key=str.casefold)


def subgrupo_existente(contas, grupo, digitado):
    """Se já existe um subgrupo igual (ignorando maiúsculas) no grupo, usa a grafia dele
    — evita 'Receita operacional' e 'receita Operacional' virarem dois subgrupos."""
    digitado = limpar_texto(digitado)
    for sub in subgrupos_do_grupo(contas, grupo):
        if sub.casefold() == digitado.casefold():
            return sub
    return digitado
