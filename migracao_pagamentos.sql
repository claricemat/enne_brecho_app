-- ============================================================
-- ENNE Brechó — Controle de pagamentos de compras (peças)
-- Rodar no SQL Editor do Supabase, de uma vez (é tudo uma transação)
-- ============================================================

BEGIN;

-- ------------------------------------------------------------
-- 1) Contas bancárias e caixa (de onde sai o dinheiro dos pagamentos)
-- ------------------------------------------------------------
CREATE TABLE conta_financeira (
    id serial PRIMARY KEY,
    nome text NOT NULL UNIQUE,
    tipo text NOT NULL CHECK (tipo IN ('banco', 'caixa')),
    criado_em timestamptz NOT NULL DEFAULT now()
);

INSERT INTO conta_financeira (nome, tipo) VALUES ('Caixa', 'caixa');

-- ------------------------------------------------------------
-- 2) Baixa (pagamento) de uma ou mais compras da mesma fornecedora
--    valor_pago = valor_bruto - desconto (calculado pelo banco)
-- ------------------------------------------------------------
CREATE TABLE baixa_compra (
    id serial PRIMARY KEY,
    fornecedora_id integer REFERENCES fornecedora(id) ON DELETE RESTRICT,
    data_pagamento date NOT NULL,
    valor_bruto numeric(10,2) NOT NULL CHECK (valor_bruto >= 0),
    desconto numeric(10,2) NOT NULL DEFAULT 0 CHECK (desconto >= 0),
    valor_pago numeric(10,2) GENERATED ALWAYS AS (valor_bruto - desconto) STORED,
    observacao text,
    criado_por text,
    criado_em timestamptz NOT NULL DEFAULT now(),
    CHECK (desconto <= valor_bruto)
);
CREATE INDEX idx_baixa_compra_data ON baixa_compra(data_pagamento);
CREATE INDEX idx_baixa_compra_fornecedora ON baixa_compra(fornecedora_id);

-- ------------------------------------------------------------
-- 3) Como cada baixa foi paga (1 ou 2 formas de pagamento)
--    A soma das formas = valor_pago é garantida pelo app
-- ------------------------------------------------------------
CREATE TABLE baixa_compra_forma (
    id serial PRIMARY KEY,
    baixa_id integer NOT NULL REFERENCES baixa_compra(id) ON DELETE CASCADE,
    forma text NOT NULL CHECK (forma IN ('conta', 'credito_loja')),
    conta_financeira_id integer REFERENCES conta_financeira(id) ON DELETE RESTRICT,
    valor numeric(10,2) NOT NULL CHECK (valor > 0),
    CHECK (
        (forma = 'conta' AND conta_financeira_id IS NOT NULL)
        OR (forma = 'credito_loja' AND conta_financeira_id IS NULL)
    )
);
CREATE INDEX idx_baixa_forma_baixa ON baixa_compra_forma(baixa_id);
CREATE INDEX idx_baixa_forma_conta ON baixa_compra_forma(conta_financeira_id);

-- ------------------------------------------------------------
-- 4) Extrato do crédito na loja das fornecedoras
--    'entrada' = crédito concedido ao pagar em crédito na loja
--    'uso'     = crédito abatido (reservado pra integrar com Vendas depois)
-- ------------------------------------------------------------
CREATE TABLE credito_loja_movimento (
    id serial PRIMARY KEY,
    fornecedora_id integer NOT NULL REFERENCES fornecedora(id) ON DELETE RESTRICT,
    tipo text NOT NULL CHECK (tipo IN ('entrada', 'uso')),
    valor numeric(10,2) NOT NULL CHECK (valor > 0),
    data date NOT NULL,
    baixa_id integer REFERENCES baixa_compra(id) ON DELETE RESTRICT,
    observacao text,
    criado_em timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_credito_loja_fornecedora ON credito_loja_movimento(fornecedora_id);

-- ------------------------------------------------------------
-- 5) compra aponta para a baixa que a quitou
--    (compras já pagas antes desta migração ficam com baixa_id nulo)
-- ------------------------------------------------------------
ALTER TABLE compra ADD COLUMN baixa_id integer REFERENCES baixa_compra(id);
CREATE INDEX idx_compra_baixa ON compra(baixa_id);

COMMIT;
