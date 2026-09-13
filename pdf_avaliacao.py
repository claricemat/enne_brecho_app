from io import BytesIO

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

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


def gerar_pdf_avaliacao(fornecedora_nome, data_avaliacao, itens):
    """Gera o PDF da proposta de compra pra uma fornecedora.

    itens: lista de dicts com descricao, tipo_peca, tamanho, aprovada,
    valor_proposto, observacao.
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

    elementos = [
        Paragraph("ENNE Brechó — Proposta de compra", titulo_estilo),
        Spacer(1, 6),
        Paragraph(f"<b>Fornecedora:</b> {fornecedora_nome}", normal),
        Paragraph(f"<b>Data da avaliação:</b> {data_avaliacao}", normal),
        Spacer(1, 14),
    ]

    cabecalho = ["Descrição", "Tipo", "Tamanho", "Status", "Valor", "Observação"]
    linhas = [cabecalho]
    total = 0.0
    for item in itens:
        aprovada = bool(item.get("aprovada"))
        valor = item.get("valor_proposto")
        valor_ok = aprovada and _numero_valido(valor)
        status_txt = "Aprovada" if aprovada else "Reprovada"
        valor_txt = f"R$ {float(valor):.2f}" if valor_ok else "—"
        if valor_ok:
            total += float(valor)
        linhas.append(
            [
                item.get("descricao") or "",
                item.get("tipo_peca") or "",
                item.get("tamanho") or "",
                status_txt,
                valor_txt,
                item.get("observacao") or "",
            ]
        )

    tabela = Table(linhas, colWidths=[4.2 * cm, 2.6 * cm, 1.8 * cm, 2.2 * cm, 2.2 * cm, 4 * cm], repeatRows=1)
    tabela.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), ROSA),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 8),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, ROSA_CLARO]),
                ("LEFTPADDING", (0, 0), (-1, -1), 5),
                ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    elementos.append(tabela)
    elementos.append(Spacer(1, 14))
    elementos.append(Paragraph(f"<b>Total proposto (peças aprovadas):</b> R$ {total:.2f}", normal))

    doc.build(elementos)
    buffer.seek(0)
    return buffer.getvalue()
