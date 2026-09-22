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

- `app.py` — página inicial (painel): só monta a tela; o conteúdo está em `painel.py`
- `painel.py` — dashboard: filtro de período, 10 cartões (estoque, vendas, margem, resultado, despesas e fornecedoras a pagar/pagas) e 4 gráficos (a pagar por dia, vendas por dia, tipos e tamanhos mais vendidos)
- `db.py` — conexão com o Postgres (Supabase) e função auxiliar `run_query`
- `pages/1_Fornecedoras.py` — cadastro de fornecedoras
- `pages/2_Compras.py` — registrar lote de compra (proposta aceita) e marcar pagamentos
- `pages/3_Vendas.py` — registrar vendas de peças em estoque (busca por código/ID, descrição ou marca)
- `pages/5_Estoque.py` — estoque + sub-aba "Gerador de etiquetas"
- `pages/6_Cadastros.py` — tipos de peça, tipos de compra, plano de contas (Grupo › Subgrupo › Analítico) e contas/caixa
- `pages/8_Financeiro.py` — conciliação bancária, importação de extrato OFX e resumo por plano de contas
- `etiquetas.py` — PDF das etiquetas 4 × 4 cm (uma por página ou grade em A4)
- `plano.py` — funções do plano de contas (grupos, subgrupos, rótulos)
- `ofx_parser.py` — leitor de arquivos OFX (versões 1 e 2)
- `financeiro.py` — importação do extrato e conciliação no banco
- `controle_pagamentos.py` — aba "Controle de pagamentos (peças)" da página de Compras (cartões, tabela filtrável, baixa com desconto e formas de pagamento combinadas)
- `exportacao_compras.py` — exporta a tabela de compras em Excel e PDF
- `pdf_avaliacao.py` — PDF com as duas propostas (A curto prazo / B longo prazo) enviado à fornecedora
- `prazos_proposta.py` — prazos das propostas (10 dias / 30 dias úteis) e cálculo do vencimento
- `formatacao.py` — valores e datas no padrão brasileiro
- `migracao_*.sql` — rodar no SQL Editor do Supabase, na ordem em que foram criadas. **Sempre rode o `.sql` novo antes de subir o código novo.** O mais recente é `migracao_financeiro.sql` (plano de contas com subgrupo + extrato bancário)

## Deploy no Streamlit Community Cloud

1. Suba este projeto num repositório do GitHub (o `.gitignore` impede o
   `secrets.toml` real de subir junto — só o `.example` vai)
2. Em share.streamlit.io, conecte o repositório
3. Em Settings → Secrets do app, cole o conteúdo do seu `secrets.toml` real
   (com a senha verdadeira)
