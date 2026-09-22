import pandas as pd
import streamlit as st

from db import run_query
from branding import aplicar_logo
from auth import exigir_login, botao_logout
from etiquetas import A4_POR_FOLHA, gerar_pdf_etiquetas
from formatacao import brl, fmt_data, hoje_brasil

st.set_page_config(page_title="Estoque", page_icon="assets/icone_coracao.png", layout="wide")
aplicar_logo()
exigir_login()
botao_logout()
st.title("Estoque")

produtos = run_query(
    """
    SELECT
        p.id,
        p.descricao,
        p.marca,
        tp.nome AS tipo_peca,
        p.tamanho,
        p.preco_custo,
        p.preco_venda,
        p.status,
        p.compra_id AS lote_id,
        tc.nome AS tipo_compra,
        COALESCE(f.nome, '—') AS fornecedora,
        c.data_aceite AS data_entrada,
        (SELECT MAX(v.data_venda)::date
           FROM item_venda iv JOIN venda v ON v.id = iv.venda_id
          WHERE iv.produto_id = p.id) AS data_venda,
        p.criado_em
    FROM produto p
    LEFT JOIN tipo_peca tp ON tp.id = p.tipo_peca_id
    JOIN compra c ON c.id = p.compra_id
    JOIN tipo_compra tc ON tc.id = c.tipo_compra_id
    LEFT JOIN fornecedora f ON f.id = c.fornecedora_id
    ORDER BY c.data_aceite DESC, p.id DESC
    """
)

hoje = hoje_brasil()
aba_estoque, aba_etiquetas = st.tabs(["Estoque", "Gerador de etiquetas"])


def _aba_estoque(produtos):
    # Dias em estoque: peça em estoque conta até hoje; peça vendida conta até o dia da venda
    for p in produtos:
        fim = p["data_venda"] if p["status"] == "vendido" else hoje
        if p["data_entrada"] is None or fim is None:
            p["dias_em_estoque"] = None
        else:
            p["dias_em_estoque"] = max((fim - p["data_entrada"]).days, 0)

    df = pd.DataFrame(produtos)
    df["dias_em_estoque"] = df["dias_em_estoque"].astype("Int64")

    col1, col2, col3, col4 = st.columns(4)
    em_estoque_df = df[df["status"] == "em_estoque"]
    col1.metric("Peças em estoque", len(em_estoque_df))
    col2.metric("Peças vendidas", len(df[df["status"] == "vendido"]))
    col3.metric("Capital parado (custo)", f"R$ {em_estoque_df['preco_custo'].sum():.2f}")
    col4.metric("Valor de venda potencial", f"R$ {em_estoque_df['preco_venda'].sum():.2f}")

    st.divider()

    busca = st.text_input("Buscar por descrição ou marca", placeholder="ex: vestido, Farm")

    col1, col2, col3, col4 = st.columns(4)
    status_filtro = col1.multiselect(
        "Status", options=["em_estoque", "vendido"], default=["em_estoque"]
    )
    tipos_disponiveis = sorted(df["tipo_peca"].dropna().unique().tolist())
    tipo_filtro = col2.multiselect("Tipo de peça", options=tipos_disponiveis)
    fornecedoras_disponiveis = sorted(df["fornecedora"].dropna().unique().tolist())
    fornecedora_filtro = col3.multiselect("Fornecedora", options=fornecedoras_disponiveis)
    marcas_disponiveis = sorted(df["marca"].dropna().unique().tolist(), key=str.casefold)
    marca_filtro = col4.multiselect("Marca", options=marcas_disponiveis)

    resultado = df.copy()
    if busca:
        resultado = resultado[
            resultado["descricao"].str.contains(busca, case=False, na=False, regex=False)
            | resultado["marca"].str.contains(busca, case=False, na=False, regex=False)
        ]
    if status_filtro:
        resultado = resultado[resultado["status"].isin(status_filtro)]
    if tipo_filtro:
        resultado = resultado[resultado["tipo_peca"].isin(tipo_filtro)]
    if fornecedora_filtro:
        resultado = resultado[resultado["fornecedora"].isin(fornecedora_filtro)]
    if marca_filtro:
        resultado = resultado[resultado["marca"].isin(marca_filtro)]

    st.caption(f"{len(resultado)} peça(s) encontrada(s)")
    st.dataframe(
        resultado[
            [
                "id",
                "descricao",
                "marca",
                "tipo_peca",
                "tamanho",
                "data_entrada",
                "dias_em_estoque",
                "preco_custo",
                "preco_venda",
                "status",
                "lote_id",
                "tipo_compra",
                "fornecedora",
            ]
        ],
        use_container_width=True,
        hide_index=True,
        column_config={
            "marca": st.column_config.TextColumn("Marca"),
            "data_entrada": st.column_config.DateColumn("Data de entrada", format="DD/MM/YYYY"),
            "dias_em_estoque": st.column_config.NumberColumn(
                "Dias em estoque",
                format="%d",
                help="Peças em estoque: dias desde a entrada até hoje. Peças vendidas: dias da entrada até a venda.",
            ),
        },
    )


def _aba_etiquetas(produtos):
    st.caption(
        "Escolha um ou mais lotes e gere um PDF com uma etiqueta de 4 × 4 cm para cada peça: "
        "logomarca, tamanho, código, valor e a mensagem de agradecimento."
    )
    incluir_vendidas = st.checkbox("Incluir peças já vendidas", value=False, key="etq_vendidas")
    elegiveis = [p for p in produtos if incluir_vendidas or p["status"] == "em_estoque"]

    lotes = {}
    for p in elegiveis:
        lotes.setdefault(p["lote_id"], []).append(p)
    if not lotes:
        st.info("Nenhuma peça em estoque para etiquetar.")
        return

    rotulos = {}
    for lote_id, pecas_lote in sorted(
        lotes.items(), key=lambda kv: (kv[1][0]["data_entrada"], kv[0]), reverse=True
    ):
        primeira = pecas_lote[0]
        origem = primeira["fornecedora"] if primeira["fornecedora"] != "—" else primeira["tipo_compra"]
        rotulos[f"Lote #{lote_id} — {origem} — {fmt_data(primeira['data_entrada'])} — {len(pecas_lote)} peça(s)"] = lote_id

    escolhidos = st.multiselect("Lotes", options=list(rotulos), key="etq_lotes")
    col1, col2 = st.columns(2)
    copias = col1.number_input(
        "Etiquetas por peça", min_value=1, max_value=10, value=1, step=1, key="etq_copias",
        help="Use mais de 1 se quiser etiquetas extras de cada peça.",
    )
    formato_rotulo = col2.radio(
        "Formato do PDF",
        ["Uma etiqueta por página (4 × 4 cm) — impressora de etiquetas", "Várias por folha A4 (recortar)"],
        key="etq_formato",
    )
    formato = "termica" if formato_rotulo.startswith("Uma etiqueta") else "a4"

    if not escolhidos:
        st.info("Selecione ao menos um lote.")
        return

    pecas = sorted(
        (p for rotulo in escolhidos for p in lotes[rotulos[rotulo]]),
        key=lambda p: (p["lote_id"], p["id"]),
    )
    total_etiquetas = len(pecas) * int(copias)
    paginas = total_etiquetas if formato == "termica" else -(-total_etiquetas // A4_POR_FOLHA)

    m1, m2, m3 = st.columns(3)
    m1.metric("Peças", len(pecas))
    m2.metric("Etiquetas", total_etiquetas)
    m3.metric("Páginas" if formato == "termica" else "Folhas A4", paginas)

    sem_preco = [p for p in pecas if not p["preco_venda"]]
    if sem_preco:
        st.warning(
            f"{len(sem_preco)} peça(s) sem preço de venda — a etiqueta sairá com R$ 0,00 "
            f"(códigos: {', '.join(str(p['id']) for p in sem_preco[:10])}{'…' if len(sem_preco) > 10 else ''})."
        )

    st.dataframe(
        [
            {
                "Lote": f"#{p['lote_id']}",
                "Código": p["id"],
                "Descrição": p["descricao"],
                "Marca": p["marca"] or "",
                "Tamanho": p["tamanho"] or "—",
                "Valor": brl(p["preco_venda"]),
            }
            for p in pecas
        ],
        use_container_width=True,
        hide_index=True,
    )

    pdf = gerar_pdf_etiquetas(
        [
            {"id": p["id"], "tamanho": p["tamanho"], "preco_venda": p["preco_venda"]}
            for p in pecas
            for _ in range(int(copias))
        ],
        formato,
    )
    lotes_no_nome = "_".join(str(lotes_id) for lotes_id in sorted({p["lote_id"] for p in pecas}))
    st.download_button(
        "Baixar PDF das etiquetas",
        data=pdf,
        file_name=f"etiquetas_lote_{lotes_no_nome}.pdf",
        mime="application/pdf",
        type="primary",
        key="etq_baixar",
    )


if not produtos:
    with aba_estoque:
        st.info("Nenhuma peça cadastrada ainda.")
    with aba_etiquetas:
        st.info("Nenhuma peça cadastrada ainda.")
else:
    with aba_estoque:
        _aba_estoque(produtos)
    with aba_etiquetas:
        _aba_etiquetas(produtos)
