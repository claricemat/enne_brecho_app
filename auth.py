import bcrypt
import streamlit as st

_CSS_LOGIN = """
<style>
[data-testid="stSidebar"],
[data-testid="collapsedControl"],
[data-testid="stSidebarCollapsedControl"] {
    display: none !important;
}
.stApp {
    background-color: #D6577A;
}
[data-testid="stForm"] {
    background-color: #FFFFFF;
    padding: 2rem 2.2rem 1.6rem 2.2rem;
    border-radius: 18px;
    box-shadow: 0 12px 32px rgba(0, 0, 0, 0.18);
    max-width: 360px;
    margin: 0 auto;
}
</style>
"""


def _verificar_senha(usuario: str, senha: str) -> bool:
    usuarios = st.secrets.get("auth_users", {})
    hash_armazenado = usuarios.get(usuario)
    if not hash_armazenado:
        return False
    return bcrypt.checkpw(senha.encode(), hash_armazenado.encode())


def _tela_login():
    st.markdown(_CSS_LOGIN, unsafe_allow_html=True)

    col_esq, col_centro, col_dir = st.columns([1, 1.2, 1])
    with col_centro:
        st.markdown("<div style='height: 8vh'></div>", unsafe_allow_html=True)

        logo_esq, logo_centro, logo_dir = st.columns([1, 1, 1])
        with logo_centro:
            st.image("assets/icone_coracao.png", width=56)

        st.markdown(
            "<h2 style='text-align:center; color:white; margin-top:-8px;'>ENNE Brechó</h2>",
            unsafe_allow_html=True,
        )

        with st.form("login"):
            st.markdown(
                "<p style='text-align:center; font-weight:600; margin-bottom:0.5rem;'>Login</p>",
                unsafe_allow_html=True,
            )
            usuario = st.text_input("Usuário")
            senha = st.text_input("Senha", type="password")
            entrar = st.form_submit_button("Entrar", use_container_width=True)

        if entrar:
            if _verificar_senha(usuario, senha):
                st.session_state.autenticado = True
                st.session_state.usuario = usuario
                st.rerun()
            else:
                st.error("Usuário ou senha incorretos.")

    st.stop()


def exigir_login():
    """Mostra a tela de login (sem sidebar) e bloqueia o resto da página até
    autenticar. Chame isso logo no início de cada página, depois de aplicar_logo()."""
    if st.session_state.get("autenticado"):
        return
    _tela_login()


def botao_logout():
    """Mostra quem está logado e um botão de sair, na sidebar (só aparece
    depois que exigir_login() já liberou a página)."""
    with st.sidebar:
        st.caption(f"Conectado como **{st.session_state.get('usuario', '')}**")
        if st.button("Sair"):
            st.session_state.autenticado = False
            st.session_state.pop("usuario", None)
            st.rerun()
