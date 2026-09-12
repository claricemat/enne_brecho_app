import streamlit as st
import psycopg2
from psycopg2.extras import RealDictCursor


def get_connection():
    """Reaproveita a conexão entre reruns da página; reabre se caiu."""
    if "conn" not in st.session_state or st.session_state.conn.closed:
        st.session_state.conn = psycopg2.connect(st.secrets["DATABASE_URL"])
    return st.session_state.conn


def _sanitize(value):
    """Converte tipos do numpy/pandas (numpy.float64, numpy.int64...) que vêm
    do data_editor para tipos nativos do Python, que o psycopg2 sabe serializar.
    """
    if hasattr(value, "item") and not isinstance(value, (str, bytes)):
        try:
            return value.item()
        except Exception:
            return value
    return value


def run_query(sql, params=None, fetch=True):
    """Executa uma query parametrizada.

    fetch=True  -> retorna as linhas (SELECT, ou INSERT ... RETURNING)
    fetch=False -> não retorna nada (INSERT/UPDATE/DELETE simples)
    """
    params = tuple(_sanitize(p) for p in params) if params else params
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(sql, params or ())
            rows = cur.fetchall() if fetch else None
        conn.commit()
        return rows
    except psycopg2.OperationalError:
        # a conexão caiu (comum em bancos gratuitos que hibernam) — reconecta e tenta de novo
        st.session_state.conn = psycopg2.connect(st.secrets["DATABASE_URL"])
        conn = st.session_state.conn
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(sql, params or ())
            rows = cur.fetchall() if fetch else None
        conn.commit()
        return rows
    except Exception:
        # desfaz a transação com erro, senão a conexão fica travada pras próximas queries
        conn.rollback()
        raise
