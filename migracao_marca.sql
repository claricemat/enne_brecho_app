-- ============================================================
-- ENNE Brechó — Marca da peça (avaliação, compra e estoque)
-- Rodar no SQL Editor do Supabase. Pode rodar mais de uma vez sem problema.
-- Peças e itens já existentes ficam sem marca.
-- ============================================================

ALTER TABLE avaliacao_item ADD COLUMN IF NOT EXISTS marca text;
ALTER TABLE produto ADD COLUMN IF NOT EXISTS marca text;
