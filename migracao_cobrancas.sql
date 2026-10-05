-- ============================================================
-- ENNE Brechó — Cobranças das fornecedoras + observação na compra
-- Rodar no SQL Editor do Supabase, de uma vez (é tudo uma transação).
-- Rodar ANTES de subir o código novo.
-- ============================================================

BEGIN;

-- 1) cada vez que uma fornecedora cobra um pagamento, fica registrado aqui.
--    O alerta conta as cobranças feitas depois do último pagamento a ela.
CREATE TABLE cobranca_fornecedora (
    id serial PRIMARY KEY,
    fornecedora_id integer NOT NULL REFERENCES fornecedora(id) ON DELETE CASCADE,
    data date NOT NULL,
    observacao text,
    criado_por text,
    criado_em timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_cobranca_fornecedora ON cobranca_fornecedora(fornecedora_id, data);

-- 2) observação livre na compra (ex.: o número da conta no controle antigo)
ALTER TABLE compra ADD COLUMN observacao text;

COMMIT;
