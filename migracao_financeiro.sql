-- ============================================================
-- ENNE Brechó — Plano de contas com subgrupo + extrato bancário (OFX)
-- Rodar no SQL Editor do Supabase, de uma vez (é tudo uma transação).
--
--   Plano de contas:  Grupo (plano_contas.tipo) > Subgrupo > Analítico (nome)
--   Ex.: Receita > Receita operacional > Venda de peças
-- ============================================================

BEGIN;

-- ------------------------------------------------------------
-- 1) Plano de contas: subgrupo e novos grupos
--    (o grupo continua na coluna "tipo", pra não mexer em Despesas/Vendas)
-- ------------------------------------------------------------

-- remove a trava antiga que só aceitava 'despesa' e 'receita'
-- (procura pelo conteúdo, não pelo nome, pra funcionar em qualquer banco)
DO $$
DECLARE
    trava record;
BEGIN
    FOR trava IN
        SELECT conname FROM pg_constraint
        WHERE conrelid = 'plano_contas'::regclass
          AND contype = 'c'
          AND pg_get_constraintdef(oid) ILIKE '%tipo%'
    LOOP
        EXECUTE format('ALTER TABLE plano_contas DROP CONSTRAINT %I', trava.conname);
    END LOOP;
END $$;

ALTER TABLE plano_contas
    ADD CONSTRAINT plano_contas_grupo_ck
    CHECK (tipo IN ('receita', 'custo', 'despesa', 'transferencia'));

ALTER TABLE plano_contas ADD COLUMN subgrupo text;

-- contas que já existiam entram num subgrupo padrão (dá pra mudar em Cadastros)
UPDATE plano_contas
SET subgrupo = CASE tipo
    WHEN 'receita' THEN 'Receita operacional'
    WHEN 'despesa' THEN 'Despesa operacional'
    ELSE 'Sem subgrupo'
END
WHERE subgrupo IS NULL;

ALTER TABLE plano_contas ALTER COLUMN subgrupo SET NOT NULL;

-- ------------------------------------------------------------
-- 2) Contas bancárias: vínculo com a conta do arquivo OFX
--    (preenchido na 1ª importação; evita importar o extrato na conta errada)
-- ------------------------------------------------------------
ALTER TABLE conta_financeira ADD COLUMN ofx_banco_id text;
ALTER TABLE conta_financeira ADD COLUMN ofx_conta_id text;

-- ------------------------------------------------------------
-- 3) Lançamentos do extrato bancário
--    valor > 0 = crédito (entrada), valor < 0 = débito (saída)
--    conciliado = tem conta do plano de contas escolhida
-- ------------------------------------------------------------
CREATE TABLE extrato_lancamento (
    id serial PRIMARY KEY,
    conta_financeira_id integer NOT NULL REFERENCES conta_financeira(id) ON DELETE RESTRICT,
    fitid text NOT NULL,                       -- identificador da transação no OFX
    data date NOT NULL,
    valor numeric(12,2) NOT NULL,
    descricao text,
    tipo_ofx text,
    plano_conta_id integer REFERENCES plano_contas(id) ON DELETE RESTRICT,
    conciliado_em timestamptz,
    conciliado_por text,
    arquivo text,
    importado_por text,
    importado_em timestamptz NOT NULL DEFAULT now(),
    UNIQUE (conta_financeira_id, fitid),       -- reimportar o mesmo extrato não duplica
    CHECK ((plano_conta_id IS NULL) = (conciliado_em IS NULL))
);
CREATE INDEX idx_extrato_conta_data ON extrato_lancamento(conta_financeira_id, data);
CREATE INDEX idx_extrato_plano_conta ON extrato_lancamento(plano_conta_id);

COMMIT;
