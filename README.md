# ENNE Brechó — Sistema de administração

## Rodando localmente

1. Instale as dependências:
   ```
   pip install -r requirements.txt
   ```

2. Copie o arquivo de exemplo de segredos e preencha com a connection string do Supabase:
   ```
   cp .streamlit/secrets.toml.example .streamlit/secrets.toml
   ```
   Pegue a connection string em Supabase → Project Settings → Database →
   Connection string (URI), e troque `[SUA-SENHA]` pela senha real do banco.

3. Rode o app:
   ```
   streamlit run app.py
   ```

## Estrutura

- `app.py` — página inicial com indicadores rápidos (peças em estoque, vendas do mês)
- `db.py` — conexão com o Postgres (Supabase) e função auxiliar `run_query`
- `pages/1_Fornecedoras.py` — cadastro de fornecedoras
- `pages/2_Compras.py` — registrar lote de compra (proposta aceita) e marcar pagamentos
- `pages/3_Vendas.py` — registrar vendas de peças em estoque
- `controle_pagamentos.py` — aba "Controle de pagamentos (peças)" da página de Compras (cartões, tabela filtrável, baixa com desconto e formas de pagamento combinadas)
- `exportacao_compras.py` — exporta a tabela de compras em Excel e PDF
- `pdf_avaliacao.py` — PDF com as duas propostas (A curto prazo / B longo prazo) enviado à fornecedora
- `prazos_proposta.py` — prazos das propostas (10 dias / 30 dias úteis) e cálculo do vencimento
- `formatacao.py` — valores e datas no padrão brasileiro
- `migracao_*.sql` — rodar no SQL Editor do Supabase, na ordem em que foram criadas

## Deploy no Streamlit Community Cloud

1. Suba este projeto num repositório do GitHub (o `.gitignore` impede o
   `secrets.toml` real de subir junto — só o `.example` vai)
2. Em share.streamlit.io, conecte o repositório
3. Em Settings → Secrets do app, cole o conteúdo do seu `secrets.toml` real
   (com a senha verdadeira)
