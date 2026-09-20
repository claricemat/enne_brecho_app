from contextlib import contextmanager

import streamlit as st
import psycopg2
from psycopg2.extras import RealDictCursor


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

    Abre uma conexão nova a cada chamada e fecha assim que termina — isso
    evita acumular conexões penduradas no pooler do Supabase, que é o
    padrão de uso recomendado pra esse tipo de pooler (conexões curtas e
    isoladas, em vez de uma conexão presa a sessão inteira do navegador).

    fetch=True  -> retorna as linhas (SELECT, ou INSERT ... RETURNING)
    fetch=False -> não retorna nada (INSERT/UPDATE/DELETE simples)
    """
    params = tuple(_sanitize(p) for p in params) if params else params
    conn = psycopg2.connect(st.secrets["DATABASE_URL"])
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(sql, params or ())
            rows = cur.fetchall() if fetch else None
        conn.commit()
        return rows
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


@contextmanager
def transacao():
    """Abre UMA conexão e roda vários comandos na mesma transação: ou tudo é
    gravado, ou (se der erro no meio) nada é.

    Uso:
        with transacao() as executar:
            linhas = executar("INSERT ... RETURNING id", (a, b), fetch=True)
            executar("UPDATE ...", (c,))

    Serve também pra ler várias consultas numa conexão só (mais rápido que
    abrir uma conexão por consulta).
    """
    conn = psycopg2.connect(st.secrets["DATABASE_URL"])
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:

            def executar(sql, params=None, fetch=False):
                params = tuple(_sanitize(p) for p in params) if params else params
                cur.execute(sql, params or ())
                return cur.fetchall() if fetch else None

            yield executar
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
