"""Etiquetas 4 x 4 cm das peças: logomarca, tamanho, código, valor e mensagem.

Dois formatos de PDF:
  - "termica": uma etiqueta por página de 4 x 4 cm (impressora de etiquetas);
  - "a4": várias etiquetas por folha A4, com linha de corte (impressora comum).
"""

from io import BytesIO
from pathlib import Path

from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import cm, mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas

from formatacao import brl

LADO = 4 * cm
MARGEM = 3 * mm
LOGO = Path(__file__).parent / "assets" / "logo_sidebar.png"

ROSA = HexColor("#D6577A")
TEXTO = HexColor("#2B2320")
CINZA = HexColor("#B8B0AC")

# folha A4: 5 colunas x 7 linhas de 4 x 4 cm
A4_COLUNAS = 5
A4_LINHAS = 7
A4_POR_FOLHA = A4_COLUNAS * A4_LINHAS

FRASE = "Obrigada pela sua compra"


def _coracao(c, cx, cy, largura, cor):
    """Coração vetorial centrado em (cx, cy)."""
    s = largura
    p = c.beginPath()
    p.moveTo(cx, cy - 0.48 * s)
    p.curveTo(cx - 0.62 * s, cy - 0.02 * s, cx - 0.58 * s, cy + 0.52 * s, cx, cy + 0.24 * s)
    p.curveTo(cx + 0.58 * s, cy + 0.52 * s, cx + 0.62 * s, cy - 0.02 * s, cx, cy - 0.48 * s)
    p.close()
    c.setFillColor(cor)
    c.drawPath(p, fill=1, stroke=0)


def _emoji_apaixonado(c, cx, cy, raio):
    """O emoji 😍 desenhado à mão (as fontes padrão do PDF não têm emojis)."""
    c.setFillColor(HexColor("#FFCB47"))
    c.setStrokeColor(HexColor("#E8A317"))
    c.setLineWidth(0.3)
    c.circle(cx, cy, raio, fill=1, stroke=1)
    for dx in (-0.4 * raio, 0.4 * raio):
        _coracao(c, cx + dx, cy + 0.22 * raio, 0.62 * raio, HexColor("#E0245E"))
    boca = c.beginPath()
    boca.moveTo(cx - 0.52 * raio, cy - 0.28 * raio)
    boca.curveTo(cx - 0.42 * raio, cy - 0.9 * raio, cx + 0.42 * raio, cy - 0.9 * raio, cx + 0.52 * raio, cy - 0.28 * raio)
    boca.close()
    c.setFillColor(HexColor("#8A3A1E"))
    c.drawPath(boca, fill=1, stroke=0)


def _fonte_que_cabe(texto, fonte, tamanho, largura_max, minimo=5):
    """Reduz o tamanho da fonte até o texto caber na largura."""
    while tamanho > minimo and stringWidth(texto, fonte, tamanho) > largura_max:
        tamanho -= 0.5
    return tamanho


def _linha_centralizada(c, cx, y, rotulo, valor, tamanho, largura_max):
    """'Rótulo: ' normal + valor em negrito, centralizados."""
    texto_total = f"{rotulo}{valor}"
    tamanho = _fonte_que_cabe(texto_total, "Helvetica-Bold", tamanho, largura_max)
    w_rotulo = stringWidth(rotulo, "Helvetica", tamanho)
    w_valor = stringWidth(valor, "Helvetica-Bold", tamanho)
    x = cx - (w_rotulo + w_valor) / 2
    c.setFillColor(TEXTO)
    c.setFont("Helvetica", tamanho)
    c.drawString(x, y, rotulo)
    c.setFont("Helvetica-Bold", tamanho)
    c.drawString(x + w_rotulo, y, valor)


def _desenhar_etiqueta(c, x, y, peca, borda=False):
    """Desenha uma etiqueta de 4 x 4 cm com o canto inferior esquerdo em (x, y)."""
    cx = x + LADO / 2
    largura_util = LADO - 2 * MARGEM

    if borda:
        c.setStrokeColor(CINZA)
        c.setLineWidth(0.3)
        c.setDash(2, 2)
        c.rect(x, y, LADO, LADO, stroke=1, fill=0)
        c.setDash()

    # logomarca
    logo = ImageReader(str(LOGO))
    largura_logo, altura_logo = logo.getSize()
    w = min(largura_util, 30 * mm)
    h = w * altura_logo / largura_logo
    topo_logo = y + LADO - MARGEM
    c.drawImage(logo, cx - w / 2, topo_logo - h, width=w, height=h, mask="auto")

    # linha fina separando a logomarca
    y_linha = topo_logo - h - 3
    c.setStrokeColor(ROSA)
    c.setLineWidth(0.6)
    c.line(x + MARGEM + 6, y_linha, x + LADO - MARGEM - 6, y_linha)

    tamanho_peca = str(peca.get("tamanho") or "—").strip() or "—"
    _linha_centralizada(c, cx, y + LADO * 0.565, "Tamanho: ", tamanho_peca, 10, largura_util)
    _linha_centralizada(c, cx, y + LADO * 0.44, "Código: ", str(peca["id"]), 10, largura_util)

    # valor
    valor = brl(peca["preco_venda"])
    tamanho_valor = _fonte_que_cabe(valor, "Helvetica-Bold", 18, largura_util)
    c.setFillColor(ROSA)
    c.setFont("Helvetica-Bold", tamanho_valor)
    c.drawCentredString(cx, y + LADO * 0.245, valor)

    # "Obrigada pela sua compra 😍!"
    fonte, tam = "Helvetica", 6.5
    tam = _fonte_que_cabe(FRASE, fonte, tam, largura_util - 14)
    raio = tam * 0.62
    w_frase = stringWidth(FRASE, fonte, tam)
    w_exclamacao = stringWidth("!", fonte, tam)
    espaco = 2
    total = w_frase + espaco + 2 * raio + w_exclamacao
    x0 = cx - total / 2
    y_texto = y + MARGEM + 3
    c.setFillColor(TEXTO)
    c.setFont(fonte, tam)
    c.drawString(x0, y_texto, FRASE)
    _emoji_apaixonado(c, x0 + w_frase + espaco + raio, y_texto + tam * 0.33, raio)
    c.setFillColor(TEXTO)
    c.setFont(fonte, tam)
    c.drawString(x0 + w_frase + espaco + 2 * raio, y_texto, "!")


def gerar_pdf_etiquetas(pecas, formato="termica"):
    """pecas: lista de dicts com id, tamanho e preco_venda (uma etiqueta por item da
    lista; repita o item para imprimir cópias). formato: 'termica' ou 'a4'.
    Retorna os bytes do PDF."""
    buffer = BytesIO()
    if formato == "a4":
        c = canvas.Canvas(buffer, pagesize=A4)
        largura_folha, altura_folha = A4
        margem_x = (largura_folha - A4_COLUNAS * LADO) / 2
        margem_y = (altura_folha - A4_LINHAS * LADO) / 2
        for i, peca in enumerate(pecas):
            posicao = i % A4_POR_FOLHA
            if i and posicao == 0:
                c.showPage()
            coluna = posicao % A4_COLUNAS
            linha = posicao // A4_COLUNAS
            x = margem_x + coluna * LADO
            y = altura_folha - margem_y - (linha + 1) * LADO
            _desenhar_etiqueta(c, x, y, peca, borda=True)
    else:
        c = canvas.Canvas(buffer, pagesize=(LADO, LADO))
        for i, peca in enumerate(pecas):
            if i:
                c.showPage()
            _desenhar_etiqueta(c, 0, 0, peca)
    c.setTitle("Etiquetas ENNE Brechó")
    c.save()
    return buffer.getvalue()
