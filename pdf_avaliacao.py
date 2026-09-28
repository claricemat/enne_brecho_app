from io import BytesIO
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from formatacao import brl, fmt_data
from prazos_proposta import PRAZO_A_VISTA_DIAS, descricao_parcelada, rotulo_parcelada

ROSA = colors.HexColor("#D6577A")
ROSA_CLARO = colors.HexColor("#FBE4E6")


def _numero_valido(v):
    """True se v for um número real utilizável (exclui None e NaN)."""
    if v is None:
        return False
    try:
        v = float(v)
    except (TypeError, ValueError):
        return False
    return v == v  # NaN nunca é igual a si mesmo


def gerar_pdf_avaliacao(fornecedora_nome, data_avaliacao, itens, config, proposta_aceita=None):
    """Gera o PDF da(s) proposta(s) de compra para uma fornecedora.

    itens: lista de dicts com descricao, tipo_peca, tamanho, aprovada,
    valor_a_vista, valor_parcelado, observacao (e, opcional, marca).
    config: dict com envia_a_vista, envia_parcelada, parcelada_qtd,
    parcelada_primeira_dias_uteis, parcelada_intervalo.
    proposta_aceita: None, 'a_vista' ou 'parcelada' (se já foi fechada, aparece no PDF).
    Retorna os bytes do PDF, prontos pro st.download_button.
    """
    envia_a_vista = bool(config["envia_a_vista"])
    envia_parcelada = bool(config["envia_parcelada"])
    duas = envia_a_vista and envia_parcelada

    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        topMargin=2 * cm,
        bottomMargin=2 * cm,
        leftMargin=1.8 * cm,
        rightMargin=1.8 * cm,
    )
    estilos = getSampleStyleSheet()
    titulo_estilo = estilos["Heading1"]
    titulo_estilo.textColor = ROSA
    normal = estilos["Normal"]
    celula = ParagraphStyle("celula", parent=normal, fontSize=8, leading=10)
    celula_dir = ParagraphStyle("celula_dir", parent=celula, alignment=2)
    celula_cabecalho = ParagraphStyle(
        "celula_cabecalho", parent=celula, textColor=colors.white, fontName="Helvetica-Bold"
    )

    def p(texto, estilo=celula):
        return Paragraph(escape(str(texto or "")), estilo)

    intro = (
        "Para as peças aprovadas abaixo, apresentamos duas propostas. "
        "<b>Apenas uma delas será fechada</b>, conforme a sua escolha."
        if duas
        else "Para as peças aprovadas abaixo, apresentamos a proposta a seguir."
    )
    elementos = [
        Paragraph("ENNE Brechó — " + ("Propostas de compra" if duas else "Proposta de compra"), titulo_estilo),
        Spacer(1, 6),
        Paragraph(f"<b>Fornecedora:</b> {escape(str(fornecedora_nome))}", normal),
        Paragraph(f"<b>Data da avaliação:</b> {fmt_data(data_avaliacao)}", normal),
        Spacer(1, 8),
        Paragraph(intro, normal),
        Spacer(1, 12),
    ]

    # ---- colunas: só as das propostas enviadas ----
    cabecalho = [p("Descrição", celula_cabecalho), p("Marca", celula_cabecalho), p("Tipo", celula_cabecalho),
                 p("Tam.", celula_cabecalho), p("Status", celula_cabecalho)]
    larguras = [3.4, 2.3, 2.1, 1.2, 1.8]
    if envia_a_vista:
        cabecalho.append(Paragraph(f"À vista<br/>(até {PRAZO_A_VISTA_DIAS} dias)", celula_cabecalho))
        larguras.append(2.3)
    if envia_parcelada:
        cabecalho.append(Paragraph(f"{escape(rotulo_parcelada(config))}<br/>(valor total)", celula_cabecalho))
        larguras.append(2.4)
    cabecalho.append(p("Observação", celula_cabecalho))
    larguras.append(17.4 - sum(larguras))

    linhas = [cabecalho]
    total_a_vista = 0.0
    total_parcelado = 0.0
    for item in itens:
        aprovada = bool(item.get("aprovada"))
        a_vista = item.get("valor_a_vista")
        parcelado = item.get("valor_parcelado")
        a_vista_ok = aprovada and _numero_valido(a_vista)
        parcelado_ok = aprovada and _numero_valido(parcelado)
        if a_vista_ok:
            total_a_vista += float(a_vista)
        if parcelado_ok:
            total_parcelado += float(parcelado)
        linha = [
            p(item.get("descricao")),
            p(item.get("marca")),
            p(item.get("tipo_peca")),
            p(item.get("tamanho")),
            p("Aprovada" if aprovada else "Reprovada"),
        ]
        if envia_a_vista:
            linha.append(p(brl(a_vista) if a_vista_ok else "—", celula_dir))
        if envia_parcelada:
            linha.append(p(brl(parcelado) if parcelado_ok else "—", celula_dir))
        linha.append(p(item.get("observacao")))
        linhas.append(linha)

    tabela = Table(linhas, colWidths=[w * cm for w in larguras], repeatRows=1)
    tabela.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), ROSA),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, ROSA_CLARO]),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    elementos.append(tabela)
    elementos.append(Spacer(1, 16))

    # ---- resumo da(s) proposta(s) ----
    resumo_linhas = []
    if envia_a_vista:
        resumo_linhas.append([
            Paragraph(f"<b>Proposta à vista</b> — pagamento em até {PRAZO_A_VISTA_DIAS} dias", normal),
            Paragraph(f"<b>Total: {brl(total_a_vista)}</b>", normal),
        ])
    if envia_parcelada:
        resumo_linhas.append([
            Paragraph(
                f"<b>Proposta parcelada</b> — {escape(descricao_parcelada(total_parcelado, config))}", normal
            ),
            Paragraph(f"<b>Total: {brl(total_parcelado)}</b>", normal),
        ])
    resumo = Table(resumo_linhas, colWidths=[12.4 * cm, 5.0 * cm])
    estilo_resumo = [
        ("BOX", (0, 0), (-1, -1), 0.8, ROSA),
        ("BACKGROUND", (0, 0), (-1, -1), ROSA_CLARO),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]
    if duas:
        estilo_resumo.append(("LINEBELOW", (0, 0), (-1, 0), 0.5, ROSA))
    resumo.setStyle(TableStyle(estilo_resumo))
    elementos.append(resumo)
    elementos.append(Spacer(1, 16))

    # ---- aceite ----
    nomes = {"a_vista": "à vista", "parcelada": "parcelada"}
    if proposta_aceita in nomes:
        elementos.append(Paragraph(f"<b>Proposta aceita:</b> {nomes[proposta_aceita]}", normal))
    elif duas:
        elementos.append(Paragraph(
            "<b>Proposta escolhida pela fornecedora:</b> "
            "(&nbsp;&nbsp;&nbsp;) À vista &nbsp;&nbsp;&nbsp; (&nbsp;&nbsp;&nbsp;) Parcelada",
            normal,
        ))
    else:
        elementos.append(Paragraph("<b>Aceite da fornecedora:</b> (&nbsp;&nbsp;&nbsp;) Aceito a proposta", normal))

    doc.build(elementos)
    buffer.seek(0)
    return buffer.getvalue()
