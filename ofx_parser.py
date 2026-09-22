"""Leitor de extratos OFX (versão 1, em SGML, e versão 2, em XML).

Os bancos brasileiros escrevem OFX de jeitos diferentes: acentos em latin-1 ou
UTF-8, vírgula no lugar do ponto nos valores, campos NAME/MEMO trocados, FITID
faltando... Este leitor tolera essas variações e não depende de bibliotecas
externas.
"""

import hashlib
import html
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation


class OfxInvalido(ValueError):
    """O arquivo não é um OFX legível ou não tem lançamentos."""


def _decodificar(conteudo):
    """OFX v1 traz o CHARSET no cabeçalho (comum: 1252 = Windows/latin-1)."""
    inicio = conteudo[:2000].decode("latin-1", errors="replace").upper()
    if "CHARSET:1252" in inicio or "CHARSET:8859-1" in inicio:
        return conteudo.decode("cp1252", errors="replace")
    for codificacao in ("utf-8-sig", "cp1252"):
        try:
            return conteudo.decode(codificacao)
        except UnicodeDecodeError:
            continue
    return conteudo.decode("latin-1", errors="replace")


def _campos(bloco):
    """Primeiro valor de cada tag do bloco. Funciona para <TAG>valor (SGML) e
    <TAG>valor</TAG> (XML)."""
    campos = {}
    for tag, valor in re.findall(r"<([A-Za-z0-9.]+)>([^<\r\n]*)", bloco):
        tag = tag.upper()
        if tag not in campos:
            campos[tag] = html.unescape(valor.strip())
    return campos


def _data(texto):
    """'20260915120000[-3:BRT]' -> date(2026, 9, 15)."""
    digitos = re.match(r"\d{8}", texto or "")
    if not digitos:
        raise OfxInvalido(f"Data inválida no arquivo: {texto!r}")
    return datetime.strptime(digitos.group(0), "%Y%m%d").date()


def _valor(texto):
    """'-1.234,56' / '-1234.56' / '-1234,56' -> Decimal('-1234.56')."""
    limpo = (texto or "").strip().replace(" ", "")
    if "," in limpo and "." in limpo:
        # o último separador é o decimal
        if limpo.rfind(",") > limpo.rfind("."):
            limpo = limpo.replace(".", "").replace(",", ".")
        else:
            limpo = limpo.replace(",", "")
    elif "," in limpo:
        limpo = limpo.replace(",", ".")
    try:
        return Decimal(limpo).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        raise OfxInvalido(f"Valor inválido no arquivo: {texto!r}")


def _descricao(nome, memo):
    """Junta NAME e MEMO sem repetir (alguns bancos põem o mesmo texto nos dois)."""
    partes = []
    for parte in (nome, memo):
        parte = " ".join((parte or "").split())
        if not parte:
            continue
        if any(parte.casefold() in p.casefold() for p in partes):
            continue
        # se o novo texto contém o anterior, ele substitui
        partes = [p for p in partes if p.casefold() not in parte.casefold()]
        partes.append(parte)
    return " — ".join(partes)


def ler_ofx(conteudo):
    """Lê os bytes de um arquivo OFX.

    Retorna dict com: banco_id, conta_id, data_inicio, data_fim, saldo,
    saldo_data e lancamentos (lista de dicts: fitid, data, valor, descricao, tipo).
    Levanta OfxInvalido se o arquivo não tiver lançamentos.
    """
    texto = _decodificar(conteudo)
    posicao = texto.upper().find("<OFX")
    if posicao < 0:
        raise OfxInvalido("Não parece um arquivo OFX (não encontrei a marca <OFX>).")
    corpo = texto[posicao:]

    blocos = re.findall(
        r"<STMTTRN>(.*?)(?=</STMTTRN>|<STMTTRN>|</BANKTRANLIST>|</STMTRS>|</CCSTMTRS>|$)",
        corpo,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if not blocos:
        raise OfxInvalido("Nenhum lançamento encontrado no arquivo.")

    # cabeçalho = tudo antes da primeira transação (conta, período, saldo)
    cabecalho_txt = corpo[: re.search(r"<STMTTRN>", corpo, flags=re.IGNORECASE).start()]
    cabecalho = _campos(cabecalho_txt)
    # o saldo vem depois da lista de transações
    saldo_txt = re.search(r"<LEDGERBAL>(.*?)(?=</LEDGERBAL>|<AVAILBAL>|$)", corpo, flags=re.IGNORECASE | re.DOTALL)
    saldo = _campos(saldo_txt.group(1)) if saldo_txt else {}

    lancamentos = []
    fitids_vistos = {}
    repetidos_sem_fitid = {}
    for bloco in blocos:
        c = _campos(bloco)
        if "DTPOSTED" not in c or "TRNAMT" not in c:
            continue  # bloco incompleto: ignora
        data = _data(c["DTPOSTED"])
        valor = _valor(c["TRNAMT"])
        descricao = _descricao(c.get("NAME"), c.get("MEMO"))

        fitid = c.get("FITID", "").strip()
        if not fitid:
            # sem FITID: cria um identificador estável a partir do conteúdo
            chave = f"{data}|{valor}|{descricao}"
            repetidos_sem_fitid[chave] = repetidos_sem_fitid.get(chave, 0) + 1
            fitid = "sem-id-" + hashlib.sha1(f"{chave}|{repetidos_sem_fitid[chave]}".encode()).hexdigest()[:16]
        else:
            # o mesmo FITID em transações diferentes (banco fora do padrão): diferencia
            conteudo_tx = (data, valor, descricao)
            if fitid in fitids_vistos and fitids_vistos[fitid][0] != conteudo_tx:
                fitids_vistos[fitid][1] += 1
                fitid = f"{fitid}-{fitids_vistos[fitid][1]}"
            else:
                fitids_vistos.setdefault(fitid, [conteudo_tx, 1])

        lancamentos.append(
            {
                "fitid": fitid,
                "data": data,
                "valor": valor,
                "descricao": descricao,
                "tipo": c.get("TRNTYPE", ""),
            }
        )

    if not lancamentos:
        raise OfxInvalido("Nenhum lançamento válido encontrado no arquivo.")

    return {
        "banco_id": cabecalho.get("BANKID", ""),
        "conta_id": cabecalho.get("ACCTID", ""),
        "data_inicio": _data(cabecalho["DTSTART"]) if cabecalho.get("DTSTART") else min(l["data"] for l in lancamentos),
        "data_fim": _data(cabecalho["DTEND"]) if cabecalho.get("DTEND") else max(l["data"] for l in lancamentos),
        "saldo": _valor(saldo["BALAMT"]) if saldo.get("BALAMT") else None,
        "saldo_data": _data(saldo["DTASOF"]) if saldo.get("DTASOF") else None,
        "lancamentos": lancamentos,
    }


def mesma_conta(a, b):
    """Compara números de conta ignorando espaços, traços e zeros à esquerda."""
    def norm(x):
        return re.sub(r"[\s\-.]", "", str(x or "")).lstrip("0")
    return norm(a) == norm(b)
