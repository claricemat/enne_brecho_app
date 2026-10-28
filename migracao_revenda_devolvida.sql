-- ============================================================
-- ENNE Brechó — Permite vender de novo uma peça que foi devolvida
-- Rodar no SQL Editor do Supabase, de uma vez (é tudo uma transação).
-- Rodar ANTES de subir o código novo.
--
-- Antes, cada peça só podia aparecer em UMA venda (item_venda_produto_id_key),
-- então a peça devolvida voltava ao estoque mas não podia ser vendida de novo.
-- Agora a peça pode estar em várias vendas, desde que só uma delas esteja
-- "valendo": a venda anterior precisa ter sido devolvida.
-- ============================================================

BEGIN;

ALTER TABLE item_venda DROP CONSTRAINT IF EXISTS item_venda_produto_id_key;

CREATE INDEX IF NOT EXISTS idx_item_venda_produto ON item_venda(produto_id);

-- proteção no banco: não deixa a mesma peça ficar em duas vendas sem devolução
CREATE OR REPLACE FUNCTION item_venda_peca_disponivel() RETURNS trigger AS $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM item_venda iv
        WHERE iv.produto_id = NEW.produto_id
          AND iv.id <> NEW.id
          AND NOT EXISTS (SELECT 1 FROM devolucao_item di WHERE di.item_venda_id = iv.id)
    ) THEN
        RAISE EXCEPTION 'A peça #% já está em outra venda que não foi devolvida.', NEW.produto_id;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_item_venda_peca_disponivel ON item_venda;
CREATE TRIGGER trg_item_venda_peca_disponivel
    BEFORE INSERT ON item_venda
    FOR EACH ROW EXECUTE FUNCTION item_venda_peca_disponivel();

COMMIT;
