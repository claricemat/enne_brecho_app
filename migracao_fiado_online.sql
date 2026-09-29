-- ============================================================
-- ENNE Brechó — Vendas na loja × online (com entrega) e cliente fiel (fiado)
-- Rodar no SQL Editor do Supabase, de uma vez (é tudo uma transação).
-- Rodar ANTES de subir o código novo.
-- ============================================================

BEGIN;

-- ------------------------------------------------------------
-- 1) Canal da venda: loja ou online. Venda online tem controle de entrega.
--    As vendas que já existem ficam como "loja".
-- ------------------------------------------------------------
ALTER TABLE venda
    ADD COLUMN canal text NOT NULL DEFAULT 'loja' CHECK (canal IN ('loja', 'online')),
    ADD COLUMN entregue boolean,
    ADD COLUMN data_entrega date;

-- entregue só existe para venda online; data de entrega só quando entregue
ALTER TABLE venda ADD CONSTRAINT venda_entrega_ck CHECK (
    ((canal = 'online') = (entregue IS NOT NULL))
    AND (data_entrega IS NULL OR entregue)
);
CREATE INDEX idx_venda_entrega ON venda(canal, entregue);

-- ------------------------------------------------------------
-- 2) Cliente fiel (fiado): forma de pagamento "Cliente fiel".
--    A venda é registrada normalmente; o que a cliente paga depois fica em
--    venda_recebimento. Saldo devedor = valor da venda − devoluções − recebimentos.
-- ------------------------------------------------------------
ALTER TABLE venda ADD CONSTRAINT venda_fiado_com_cliente_ck
    CHECK (forma_pagamento IS DISTINCT FROM 'Cliente fiel' OR NULLIF(btrim(cliente), '') IS NOT NULL);
CREATE INDEX idx_venda_forma ON venda(forma_pagamento);

CREATE TABLE venda_recebimento (
    id serial PRIMARY KEY,
    venda_id integer NOT NULL REFERENCES venda(id) ON DELETE RESTRICT,
    data date NOT NULL,
    valor numeric(10,2) NOT NULL CHECK (valor > 0),
    forma text NOT NULL,
    lote uuid NOT NULL,          -- um pagamento da cliente pode quitar várias vendas
    observacao text,
    criado_por text,
    criado_em timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_recebimento_venda ON venda_recebimento(venda_id);
CREATE INDEX idx_recebimento_lote ON venda_recebimento(lote);

COMMIT;
