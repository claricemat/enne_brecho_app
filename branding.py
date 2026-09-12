import streamlit as st


def aplicar_logo():
    """Mostra o coração + ENNE Brechó no topo da sidebar (e o coração
    sozinho no corpo principal, quando a sidebar está colapsada)."""
    st.logo(
        "assets/logo_sidebar.png",
        icon_image="assets/icone_coracao.png",
    )
