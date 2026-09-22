from io import BytesIO
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from formatacao import brl, fmt_data
from prazos_proposta import PRAZO_CURTO_DIAS, PRAZO_LONGO_DIAS_UTEIS

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


def gerar_pdf_avaliacao(fornecedora_nome, data_avaliacao, itens, proposta_aceita=None):
    """Gera o PDF com as DUAS propostas de compra pra uma fornecedora.

    itens: lista de dicts com descricao, tipo_peca, tamanho, aprovada,
    valor_curto_prazo, valor_longo_prazo, observacao (e, opcional, marca).
    proposta_aceita: None, 'curto' ou 'longo' (se já foi fechada, aparece no PDF).
    Retorna os bytes do PDF, prontos pro st.download_button.
    """
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
    celula_cabecalho = ParagraphStyle(
        "celula_cabecalho", parent=celula, textColor=colors.white, fontName="Helvetica-Bold"
    )

    def p(texto, estilo=celula):
        return Paragraph(escape(str(texto or "")), estilo)

    elementos = [
        Paragraph("ENNE Brechó — Propostas de compra", titulo_estilo),
        Spacer(1, 6),
        Paragraph(f"<b>Fornecedora:</b> {escape(str(fornecedora_nome))}", normal),
        Paragraph(f"<b>Data da avaliação:</b> {fmt_data(data_avaliacao)}", normal),
        Spacer(1, 8),
        Paragraph(
            "Para as peças aprovadas abaixo, apresentamos duas propostas. "
            "<b>Apenas uma delas será fechada</b>, conforme a sua escolha.",
            normal,
        ),
        Spacer(1, 12),
    ]

    cabecalho = [
        p("Descrição", celula_cabecalho),
        p("Marca", celula_cabecalho),
        p("Tipo", celula_cabecalho),
        p("Tam.", celula_cabecalho),
        p("Status", celula_cabecalho),
        Paragraph(f"Proposta A<br/>(até {PRAZO_CURTO_DIAS} dias)", celula_cabecalho),
        Paragraph(f"Proposta B<br/>(até {PRAZO_LONGO_DIAS_UTEIS} dias úteis)", celula_cabecalho),
        p("Observação", celula_cabecalho),
    ]
    linhas = [cabecalho]
    total_curto = 0.0
    total_longo = 0.0
    for item in itens:
        aprovada = bool(item.get("aprovada"))
        curto = item.get("valor_curto_prazo")
        longo = item.get("valor_longo_prazo")
        curto_ok = aprovada and _numero_valido(curto)
        longo_ok = aprovada and _numero_valido(longo)
        if curto_ok:
            total_curto += float(curto)
        if longo_ok:
            total_longo += float(longo)
        linhas.append(
            [
                p(item.get("descricao")),
                p(item.get("marca")),
                p(item.get("tipo_peca")),
                p(item.get("tamanho")),
                p("Aprovada" if aprovada else "Reprovada"),
                p(brl(curto) if curto_ok else "—"),
                p(brl(longo) if longo_ok else "—"),
                p(item.get("observacao")),
            ]
        )

    tabela = Table(
        linhas,
        colWidths=[3.2 * cm, 2.2 * cm, 2.0 * cm, 1.2 * cm, 1.8 * cm, 2.3 * cm, 2.5 * cm, 2.2 * cm],
        repeatRows=1,
    )
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

    # ---- resumo das duas propostas ----
    resumo = Table(
        [
            [
                Paragraph(f"<b>Proposta A</b> — pagamento em até {PRAZO_CURTO_DIAS} dias", normal),
                Paragraph(f"<b>Total: {brl(total_curto)}</b>", normal),
            ],
            [
                Paragraph(f"<b>Proposta B</b> — pagamento em até {PRAZO_LONGO_DIAS_UTEIS} dias úteis", normal),
                Paragraph(f"<b>Total: {brl(total_longo)}</b>", normal),
            ],
        ],
        colWidths=[11.5 * cm, 5.9 * cm],
    )
    resumo.setStyle(
        TableStyle(
            [
                ("BOX", (0, 0), (-1, -1), 0.8, ROSA),
                ("LINEBELOW", (0, 0), (-1, 0), 0.5, ROSA),
                ("BACKGROUND", (0, 0), (-1, -1), ROSA_CLARO),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ]
        )
    )
    elementos.append(resumo)
    elementos.append(Spacer(1, 16))

    if proposta_aceita in ("curto", "longo"):
        nome = "A (curto prazo)" if proposta_aceita == "curto" else "B (longo prazo)"
        elementos.append(Paragraph(f"<b>Proposta aceita:</b> {nome}", normal))
    else:
        elementos.append(
            Paragraph("<b>Proposta escolhida pela fornecedora:</b> (&nbsp;&nbsp;&nbsp;) A &nbsp;&nbsp;&nbsp; (&nbsp;&nbsp;&nbsp;) B", normal)
        )

    doc.build(elementos)
    buffer.seek(0)
    return buffer.getvalue()
