import bcrypt
import streamlit as st


def _verificar_senha(usuario: str, senha: str) -> bool:
    usuarios = st.secrets.get("auth_users", {})
    hash_armazenado = usuarios.get(usuario)
    if not hash_armazenado:
        return False
    return bcrypt.checkpw(senha.encode(), hash_armazenado.encode())


def exigir_login():
    """Mostra uma tela de login e bloqueia o resto da página até autenticar.
    Chame isso logo no início de cada página, depois de aplicar_logo()."""
    if st.session_state.get("autenticado"):
        return

    st.title("ENNE Brechó")
    st.subheader("Login")
    with st.form("login"):
        usuario = st.text_input("Usuário")
        senha = st.text_input("Senha", type="password")
        entrar = st.form_submit_button("Entrar")

    if entrar:
        if _verificar_senha(usuario, senha):
            st.session_state.autenticado = True
            st.session_state.usuario = usuario
            st.rerun()
        else:
            st.error("Usuário ou senha incorretos.")

    st.stop()


def botao_logout():
    """Mostra quem está logado e um botão de sair, na sidebar."""
    with st.sidebar:
        st.caption(f"Conectado como **{st.session_state.get('usuario', '')}**")
        if st.button("Sair"):
            st.session_state.autenticado = False
            st.session_state.pop("usuario", None)
            st.rerun()
