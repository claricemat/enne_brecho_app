-- ============================================================
-- ENNE Brechó — Despesas: conta/caixa do pagamento e data em que foi paga
-- Rodar no SQL Editor do Supabase, de uma vez (é tudo uma transação).
-- Rodar ANTES de subir o código novo.
--
-- As despesas que já estão pagas ficam sem conta e sem data de pagamento
-- (aparecem como "não informado"); as novas passam a exigir os dois.
-- ============================================================

BEGIN;

ALTER TABLE despesa
    ADD COLUMN conta_financeira_id integer REFERENCES conta_financeira(id) ON DELETE RESTRICT,
    ADD COLUMN data_pagamento date;

-- conta e data de pagamento só fazem sentido em despesa paga
ALTER TABLE despesa ADD CONSTRAINT despesa_pagamento_ck CHECK (
    status_pagamento = 'pago' OR (conta_financeira_id IS NULL AND data_pagamento IS NULL)
);

CREATE INDEX idx_despesa_conta_financeira ON despesa(conta_financeira_id);

COMMIT;
