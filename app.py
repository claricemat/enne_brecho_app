import streamlit as st

from auth import exigir_login, botao_logout
from branding import aplicar_logo
from painel import renderizar_painel

st.set_page_config(page_title="ENNE Brechó", page_icon="assets/icone_coracao.png", layout="wide")
aplicar_logo()
exigir_login()
botao_logout()

st.title("ENNE Brechó — Painel")
renderizar_painel()
