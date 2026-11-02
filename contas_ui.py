"""Campo de tela "em qual conta/caixa entrou (ou saiu) o dinheiro", usado em Vendas.

Já vem preenchido com a conta padrão da forma de pagamento
(Financeiro → Configurações → Conta padrão de cada forma de pagamento).
"""

import streamlit as st

from financeiro import (
    FORMAS_COM_CONTA,
    FORMAS_CONTA_OBRIGATORIA,
    contas_financeiras,
    contas_padrao,
    rotulo_conta_financeira,
)


def carregar_contas_e_padroes():
    contas = contas_financeiras()
    return contas, contas_padrao()


def seletor_conta(forma, contas, padroes, chave, rotulo="Recebido em", forma_padrao=None, coluna=None):
    """Mostra o campo só para formas que movimentam dinheiro. Retorna (conta_id, erro)."""
    if forma not in FORMAS_COM_CONTA:
        return None, None
    alvo = coluna or st
    if not contas:
        erro = "Cadastre o caixa ou uma conta bancária em Cadastros → Contas e caixa."
        alvo.warning(erro)
        return None, erro if forma in FORMAS_CONTA_OBRIGATORIA else None
    opcoes = {rotulo_conta_financeira(c): c["id"] for c in contas}
    ids = list(opcoes.values())
    padrao = padroes.get(forma_padrao or forma)
    obrigatoria = forma in FORMAS_CONTA_OBRIGATORIA
    escolha = alvo.selectbox(
        f"{rotulo}{'*' if obrigatoria else ' (opcional)'}",
        list(opcoes),
        index=ids.index(padrao) if padrao in ids else None,
        placeholder="Escolha a conta ou o caixa",
        key=f"{chave}_{forma}",
        help="Conta bancária ou caixa onde o dinheiro entrou. É o que liga a venda ao extrato do banco "
             "na conciliação. A conta padrão de cada forma se escolhe em Financeiro → Configurações.",
    )
    if escolha is None:
        return None, (f"Informe em qual conta ou caixa entrou o pagamento em {forma}." if obrigatoria else None)
    return opcoes[escolha], None
