-- ============================================================
-- ENNE Brechó — Financeiro integrado com Vendas, Despesas e Compras
-- Rodar no SQL Editor do Supabase, de uma vez (é tudo uma transação).
-- Rodar ANTES de subir o código novo.
--
-- O que muda:
--   1) Venda (cada forma de pagamento), recebimento do fiado e reembolso de
--      devolução passam a guardar EM QUAL CONTA/CAIXA o dinheiro entrou/saiu.
--   2) Conta padrão por forma de pagamento (ex.: Dinheiro -> Caixa, Pix -> banco).
--   3) Saldo inicial de cada conta/caixa (para o balancete e o saldo do caixa).
--   4) Lançamentos manuais (entradas/saídas do caixa, transferências entre contas).
--   5) Vínculo entre o lançamento do extrato (OFX) e o movimento do sistema
--      (venda, despesa, pagamento de fornecedora...) — a conciliação automática.
--   6) A view movimento_financeiro junta tudo o que entra e sai de cada conta.
--
-- Nada é apagado. As vendas antigas ficam sem conta (dá para preencher em
-- Financeiro → Configurações → "Lançamentos sem conta").
-- ============================================================

BEGIN;

-- ------------------------------------------------------------
-- 1) Conta/caixa nas vendas, recebimentos do fiado e devoluções
-- ------------------------------------------------------------
ALTER TABLE venda_pagamento
    ADD COLUMN conta_financeira_id integer REFERENCES conta_financeira(id) ON DELETE RESTRICT;
ALTER TABLE venda_recebimento
    ADD COLUMN conta_financeira_id integer REFERENCES conta_financeira(id) ON DELETE RESTRICT;
ALTER TABLE devolucao
    ADD COLUMN conta_financeira_id integer REFERENCES conta_financeira(id) ON DELETE RESTRICT;

CREATE INDEX idx_venda_pagamento_conta ON venda_pagamento(conta_financeira_id);
CREATE INDEX idx_venda_recebimento_conta ON venda_recebimento(conta_financeira_id);
CREATE INDEX idx_devolucao_conta ON devolucao(conta_financeira_id);
CREATE INDEX IF NOT EXISTS idx_despesa_data_pagamento ON despesa(data_pagamento);

-- ------------------------------------------------------------
-- 2) Conta padrão de cada forma de pagamento
-- ------------------------------------------------------------
CREATE TABLE forma_pagamento_conta (
    forma               text PRIMARY KEY,
    conta_financeira_id integer NOT NULL REFERENCES conta_financeira(id) ON DELETE CASCADE
);

-- dinheiro entra no caixa (se existir uma conta do tipo caixa chamada "Caixa")
INSERT INTO forma_pagamento_conta (forma, conta_financeira_id)
SELECT 'Dinheiro', id FROM conta_financeira WHERE tipo = 'caixa' AND lower(nome) = 'caixa'
ORDER BY id LIMIT 1;

-- ------------------------------------------------------------
-- 3) Saldo inicial das contas e do caixa
-- ------------------------------------------------------------
ALTER TABLE conta_financeira
    ADD COLUMN saldo_inicial numeric(12,2) NOT NULL DEFAULT 0,
    ADD COLUMN saldo_inicial_data date;

-- ------------------------------------------------------------
-- 4) Contas do plano usadas automaticamente
--    (pagamento de compras de peças e transferências entre contas)
-- ------------------------------------------------------------
INSERT INTO plano_contas (tipo, subgrupo, nome)
VALUES ('custo', 'Custo das mercadorias', 'Compra de peças')
ON CONFLICT (tipo, nome) DO NOTHING;

INSERT INTO plano_contas (tipo, subgrupo, nome)
VALUES ('transferencia', 'Transferências', 'Transferência entre contas')
ON CONFLICT (tipo, nome) DO NOTHING;

CREATE TABLE config_financeiro (
    chave          text PRIMARY KEY,
    plano_conta_id integer REFERENCES plano_contas(id) ON DELETE SET NULL
);

INSERT INTO config_financeiro (chave, plano_conta_id)
SELECT 'pagamento_compra', id FROM plano_contas WHERE tipo = 'custo' AND nome = 'Compra de peças';
INSERT INTO config_financeiro (chave, plano_conta_id)
SELECT 'transferencia', id FROM plano_contas WHERE tipo = 'transferencia' AND nome = 'Transferência entre contas';

-- ------------------------------------------------------------
-- 5) Lançamentos manuais (caixa, transferências, ajustes da conciliação)
--    valor > 0 = entrada, valor < 0 = saída
-- ------------------------------------------------------------
CREATE TABLE movimento_manual (
    id                    serial PRIMARY KEY,
    conta_financeira_id   integer NOT NULL REFERENCES conta_financeira(id) ON DELETE RESTRICT,
    data                  date NOT NULL,
    valor                 numeric(12,2) NOT NULL CHECK (valor <> 0),
    plano_conta_id        integer REFERENCES plano_contas(id) ON DELETE RESTRICT,
    descricao             text,
    transferencia         uuid,      -- as duas pontas de uma transferência têm o mesmo código
    extrato_lancamento_id integer REFERENCES extrato_lancamento(id) ON DELETE CASCADE,  -- ajuste criado na conciliação
    criado_por            text,
    criado_em             timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_movimento_manual_conta_data ON movimento_manual(conta_financeira_id, data);
CREATE INDEX idx_movimento_manual_transferencia ON movimento_manual(transferencia);

-- ------------------------------------------------------------
-- 6) Conciliação: lançamento do extrato <-> movimento(s) do sistema
-- ------------------------------------------------------------
CREATE TABLE conciliacao_vinculo (
    id                    serial PRIMARY KEY,
    extrato_lancamento_id integer NOT NULL REFERENCES extrato_lancamento(id) ON DELETE CASCADE,
    origem                text NOT NULL CHECK (origem IN ('venda', 'fiado', 'despesa', 'fornecedora', 'devolucao', 'manual')),
    origem_id             integer NOT NULL,
    valor                 numeric(12,2) NOT NULL,
    UNIQUE (origem, origem_id)          -- cada movimento só é conciliado uma vez
);
CREATE INDEX idx_conciliacao_extrato ON conciliacao_vinculo(extrato_lancamento_id);

-- o lançamento do extrato agora pode estar conciliado com o sistema (sem conta do
-- plano direto nele) ou classificado direto numa conta do plano (tarifa, por ex.)
DO $$
DECLARE
    trava record;
BEGIN
    FOR trava IN
        SELECT conname FROM pg_constraint
        WHERE conrelid = 'extrato_lancamento'::regclass
          AND contype = 'c'
          AND pg_get_constraintdef(oid) ILIKE '%conciliado_em%'
    LOOP
        EXECUTE format('ALTER TABLE extrato_lancamento DROP CONSTRAINT %I', trava.conname);
    END LOOP;
END $$;

ALTER TABLE extrato_lancamento
    ADD CONSTRAINT extrato_conciliado_ck CHECK (plano_conta_id IS NULL OR conciliado_em IS NOT NULL);

-- ------------------------------------------------------------
-- 7) Tudo o que entra (+) e sai (−) de cada conta/caixa, registrado no sistema
-- ------------------------------------------------------------
CREATE VIEW movimento_financeiro AS
SELECT 'venda'::text AS origem, vp.id AS origem_id, vp.conta_financeira_id,
       (v.data_venda AT TIME ZONE 'America/Sao_Paulo')::date AS data,
       vp.valor::numeric AS valor, v.plano_conta_id,
       'Venda #' || v.id || ' — ' || vp.forma || COALESCE(' — ' || v.cliente, '') AS descricao,
       vp.forma
FROM venda_pagamento vp
JOIN venda v ON v.id = vp.venda_id
WHERE vp.conta_financeira_id IS NOT NULL AND vp.valor > 0

UNION ALL
SELECT 'fiado', r.id, r.conta_financeira_id, r.data, r.valor::numeric, v.plano_conta_id,
       'Recebimento fiado — venda #' || v.id || COALESCE(' — ' || v.cliente, '') || ' — ' || r.forma,
       r.forma
FROM venda_recebimento r
JOIN venda v ON v.id = r.venda_id
WHERE r.conta_financeira_id IS NOT NULL

UNION ALL
SELECT 'despesa', d.id, d.conta_financeira_id, d.data_pagamento, -d.valor::numeric, d.plano_conta_id,
       'Despesa — ' || pc.nome || COALESCE(' — ' || d.descricao, '')
           || CASE WHEN d.parcela_total > 1 THEN ' (' || d.parcela_numero || '/' || d.parcela_total || ')' ELSE '' END,
       NULL
FROM despesa d
JOIN plano_contas pc ON pc.id = d.plano_conta_id
WHERE d.status_pagamento = 'pago' AND d.conta_financeira_id IS NOT NULL AND d.data_pagamento IS NOT NULL

UNION ALL
SELECT 'fornecedora', bf.id, bf.conta_financeira_id, b.data_pagamento, -bf.valor::numeric,
       (SELECT cfg.plano_conta_id FROM config_financeiro cfg WHERE cfg.chave = 'pagamento_compra'),
       'Pagamento de compra — ' || COALESCE(f.nome, 'sem fornecedora')
           || COALESCE(' — ' || b.observacao, ''),
       NULL
FROM baixa_compra_forma bf
JOIN baixa_compra b ON b.id = bf.baixa_id
LEFT JOIN fornecedora f ON f.id = b.fornecedora_id
WHERE bf.forma = 'conta' AND bf.conta_financeira_id IS NOT NULL

UNION ALL
SELECT 'devolucao', d.id, d.conta_financeira_id, d.data, -d.valor_devolvido::numeric, v.plano_conta_id,
       'Devolução — venda #' || v.id || COALESCE(' — ' || v.cliente, '') || ' — ' || d.forma_reembolso,
       d.forma_reembolso
FROM devolucao d
JOIN venda v ON v.id = d.venda_id
WHERE d.conta_financeira_id IS NOT NULL AND d.valor_devolvido > 0

UNION ALL
SELECT 'manual', m.id, m.conta_financeira_id, m.data, m.valor::numeric, m.plano_conta_id,
       COALESCE(m.descricao, 'Lançamento manual'),
       NULL
FROM movimento_manual m;

COMMIT;
