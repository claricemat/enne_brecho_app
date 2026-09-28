-- ============================================================
-- ENNE Brechó — Propostas da avaliação: à vista e parcelada
-- Rodar no SQL Editor do Supabase, de uma vez (é tudo uma transação).
-- Rodar ANTES de subir o código novo.
--
--   Proposta à vista  (antiga "A — curto prazo"): pagamento em até 10 dias
--   Proposta parcelada (antiga "B — longo prazo"): N parcelas, a 1ª em X dias
--                      úteis após o aceite, as demais no intervalo escolhido
--   Cada avaliação pode enviar só uma delas ou as duas.
--
--   Avaliações antigas: as duas propostas continuam valendo, e a antiga B vira
--   "parcelada em 1x, com a parcela em 30 dias úteis" (exatamente a regra dela).
-- ============================================================

BEGIN;

-- ------------------------------------------------------------
-- 1) Itens: nomes das colunas de valor acompanham os nomes das propostas
-- ------------------------------------------------------------
ALTER TABLE avaliacao_item RENAME COLUMN valor_curto_prazo TO valor_a_vista;
ALTER TABLE avaliacao_item RENAME COLUMN valor_longo_prazo TO valor_parcelado;

-- as travas antigas exigiam as duas propostas em toda peça aprovada;
-- agora basta ter valor na(s) proposta(s) enviada(s) — o app confere qual
DO $$
DECLARE
    trava record;
BEGIN
    FOR trava IN
        SELECT conname FROM pg_constraint
        WHERE conrelid = 'avaliacao_item'::regclass
          AND contype = 'c'
          AND pg_get_constraintdef(oid) ILIKE '%aprovada%'
    LOOP
        EXECUTE format('ALTER TABLE avaliacao_item DROP CONSTRAINT %I', trava.conname);
    END LOOP;
END $$;

ALTER TABLE avaliacao_item ADD CONSTRAINT avaliacao_item_aprovada_com_valor_ck
    CHECK (NOT aprovada OR valor_a_vista IS NOT NULL OR valor_parcelado IS NOT NULL);

-- ------------------------------------------------------------
-- 2) Avaliação: quais propostas vão para a fornecedora e as condições da parcelada
-- ------------------------------------------------------------
ALTER TABLE avaliacao
    ADD COLUMN envia_a_vista boolean NOT NULL DEFAULT true,
    ADD COLUMN envia_parcelada boolean NOT NULL DEFAULT true,
    ADD COLUMN parcelada_qtd integer NOT NULL DEFAULT 1
        CHECK (parcelada_qtd BETWEEN 1 AND 24),
    ADD COLUMN parcelada_primeira_dias_uteis integer NOT NULL DEFAULT 30
        CHECK (parcelada_primeira_dias_uteis BETWEEN 0 AND 120),
    ADD COLUMN parcelada_intervalo text NOT NULL DEFAULT 'mensal'
        CHECK (parcelada_intervalo IN ('mensal', '30d', '15d', '7d'));

ALTER TABLE avaliacao ADD CONSTRAINT avaliacao_alguma_proposta_ck
    CHECK (envia_a_vista OR envia_parcelada);

-- ------------------------------------------------------------
-- 3) Proposta aceita: 'curto'/'longo' viram 'a_vista'/'parcelada'
-- ------------------------------------------------------------
DO $$
DECLARE
    trava record;
BEGIN
    FOR trava IN
        SELECT conname FROM pg_constraint
        WHERE conrelid = 'avaliacao'::regclass
          AND contype = 'c'
          AND pg_get_constraintdef(oid) ILIKE '%curto%'
    LOOP
        EXECUTE format('ALTER TABLE avaliacao DROP CONSTRAINT %I', trava.conname);
    END LOOP;
END $$;

UPDATE avaliacao SET proposta_aceita = 'a_vista' WHERE proposta_aceita = 'curto';
UPDATE avaliacao SET proposta_aceita = 'parcelada' WHERE proposta_aceita = 'longo';

-- só pode ser aceita uma proposta que foi enviada
ALTER TABLE avaliacao ADD CONSTRAINT avaliacao_proposta_aceita_valida_ck CHECK (
    proposta_aceita IS NULL
    OR (proposta_aceita = 'a_vista' AND envia_a_vista)
    OR (proposta_aceita = 'parcelada' AND envia_parcelada)
);

COMMIT;
