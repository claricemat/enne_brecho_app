-- ============================================================
-- ENNE Brechó — Avaliação com duas propostas (curto e longo prazo)
-- Rodar no SQL Editor do Supabase, de uma vez (é tudo uma transação)
--
--   Proposta A (curto prazo): pagamento em até 10 dias
--   Proposta B (longo prazo): pagamento em até 30 dias úteis
--   A fornecedora escolhe uma; a escolhida fica em avaliacao.proposta_aceita
-- ============================================================

BEGIN;

-- ------------------------------------------------------------
-- 1) Itens: o valor antigo (único) passa a ser o de longo prazo
--    (as avaliações antigas eram pagas em ~30 dias) e entra o de curto prazo
-- ------------------------------------------------------------
ALTER TABLE avaliacao_item RENAME COLUMN valor_proposto TO valor_longo_prazo;

ALTER TABLE avaliacao_item
    ADD COLUMN valor_curto_prazo numeric(10,2)
    CHECK (valor_curto_prazo IS NULL OR valor_curto_prazo >= 0);

-- avaliações já existentes: começam com as duas propostas iguais
-- (é só editar a avaliação pra ajustar o valor de curto prazo)
UPDATE avaliacao_item SET valor_curto_prazo = valor_longo_prazo WHERE aprovada;

ALTER TABLE avaliacao_item
    ADD CONSTRAINT avaliacao_item_curto_prazo_ck
    CHECK (NOT aprovada OR valor_curto_prazo IS NOT NULL);

-- ------------------------------------------------------------
-- 2) Avaliação: qual proposta a fornecedora aceitou
-- ------------------------------------------------------------
ALTER TABLE avaliacao
    ADD COLUMN proposta_aceita text CHECK (proposta_aceita IN ('curto', 'longo'));

-- avaliações já aceitas antes desta mudança eram de ~30 dias
UPDATE avaliacao SET proposta_aceita = 'longo' WHERE status = 'aceita';

ALTER TABLE avaliacao
    ADD CONSTRAINT avaliacao_proposta_aceita_ck
    CHECK ((status = 'aceita') = (proposta_aceita IS NOT NULL));

COMMIT;
