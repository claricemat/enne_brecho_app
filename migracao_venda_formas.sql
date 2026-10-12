-- ============================================================
-- ENNE Brechó — Venda com até duas formas de pagamento
-- Rodar no SQL Editor do Supabase, de uma vez (é tudo uma transação).
-- Rodar ANTES de subir o código novo.
--
-- Cada venda passa a ter as formas de pagamento (e o valor em cada uma)
-- na tabela venda_pagamento. As vendas que já existem ganham uma linha
-- com a forma delas e o valor total. A coluna venda.forma_pagamento
-- continua existindo, com o texto que aparece nas telas
-- (ex.: "Pix" ou "Pix + Dinheiro").
-- ============================================================

BEGIN;

CREATE TABLE venda_pagamento (
    id        serial PRIMARY KEY,
    venda_id  integer NOT NULL REFERENCES venda(id) ON DELETE CASCADE,
    forma     text NOT NULL,
    valor     numeric(10,2) NOT NULL CHECK (valor >= 0)
);

CREATE INDEX idx_venda_pagamento_venda ON venda_pagamento(venda_id);
CREATE INDEX idx_venda_pagamento_forma ON venda_pagamento(forma);

-- vendas que já existem: uma forma só, com o valor total
INSERT INTO venda_pagamento (venda_id, forma, valor)
SELECT id, COALESCE(NULLIF(btrim(forma_pagamento), ''), 'Outro'), valor_total
FROM venda
ORDER BY id;

-- fiado (mesmo que seja só uma parte da venda) continua exigindo o nome da cliente
ALTER TABLE venda DROP CONSTRAINT IF EXISTS venda_fiado_com_cliente_ck;
ALTER TABLE venda ADD CONSTRAINT venda_fiado_com_cliente_ck CHECK (
    position('Cliente fiel' in COALESCE(forma_pagamento, '')) = 0
    OR NULLIF(btrim(cliente), '') IS NOT NULL
);

COMMIT;

-- Conferência (opcional): as duas contagens devem ser iguais
-- SELECT (SELECT COUNT(*) FROM venda) AS vendas, (SELECT COUNT(*) FROM venda_pagamento) AS formas;
