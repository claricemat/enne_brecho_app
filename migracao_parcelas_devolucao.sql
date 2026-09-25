-- ============================================================
-- ENNE Brechó — Parcelamento de compras e despesas + devolução de peças
-- Rodar no SQL Editor do Supabase, de uma vez (é tudo uma transação).
-- Rodar ANTES de subir o código novo.
-- ============================================================

BEGIN;

-- ------------------------------------------------------------
-- 1) Parcelas das compras de peças
--    Toda compra passa a ter 1 ou mais parcelas. O pagamento (baixa) é
--    dado por parcela. As compras que já existem viram 1 parcela cada.
-- ------------------------------------------------------------
CREATE TABLE compra_parcela (
    id serial PRIMARY KEY,
    compra_id integer NOT NULL REFERENCES compra(id) ON DELETE CASCADE,
    numero integer NOT NULL CHECK (numero >= 1),
    valor numeric(10,2) NOT NULL CHECK (valor >= 0),
    data_vencimento date NOT NULL,
    status text NOT NULL DEFAULT 'pendente' CHECK (status IN ('pendente', 'pago')),
    data_pagamento date,
    baixa_id integer REFERENCES baixa_compra(id) ON DELETE RESTRICT,
    CHECK ((status = 'pago') = (data_pagamento IS NOT NULL)),
    -- deferrable: permite renumerar as parcelas numa única instrução
    CONSTRAINT compra_parcela_numero_uk UNIQUE (compra_id, numero) DEFERRABLE INITIALLY DEFERRED
);
CREATE INDEX idx_parcela_compra ON compra_parcela(compra_id);
CREATE INDEX idx_parcela_vencimento ON compra_parcela(status, data_vencimento);
CREATE INDEX idx_parcela_baixa ON compra_parcela(baixa_id);

-- compras existentes: 1 parcela com o valor, vencimento e situação de hoje
INSERT INTO compra_parcela (compra_id, numero, valor, data_vencimento, status, data_pagamento, baixa_id)
SELECT id, 1, valor_total,
       COALESCE(data_vencimento, data_aceite),
       CASE WHEN status = 'pago' THEN 'pago' ELSE 'pendente' END,
       CASE WHEN status = 'pago' THEN COALESCE(data_pagamento, data_aceite) END,
       CASE WHEN status = 'pago' THEN baixa_id END
FROM compra;

-- A compra continua tendo status / vencimento / data de pagamento, agora
-- calculados a partir das parcelas (automático, a cada mudança nelas):
--   status          = 'pago' só quando todas as parcelas estão pagas
--   data_vencimento = vencimento da próxima parcela em aberto (ou da última)
--   data_pagamento  = data do último pagamento, quando está toda paga
CREATE OR REPLACE FUNCTION sincronizar_compra_com_parcelas() RETURNS trigger AS $$
DECLARE
    alvo integer;
BEGIN
    IF TG_OP = 'DELETE' THEN
        alvo := OLD.compra_id;
    ELSE
        alvo := NEW.compra_id;
    END IF;

    UPDATE compra c
    SET status          = CASE WHEN s.pendentes = 0 THEN 'pago' ELSE 'pendente' END,
        data_vencimento = COALESCE(s.proximo_vencimento, s.ultimo_vencimento),
        data_pagamento  = CASE WHEN s.pendentes = 0 THEN s.ultimo_pagamento END
    FROM (
        SELECT COUNT(*) AS total,
               COUNT(*) FILTER (WHERE status = 'pendente') AS pendentes,
               MIN(data_vencimento) FILTER (WHERE status = 'pendente') AS proximo_vencimento,
               MAX(data_vencimento) AS ultimo_vencimento,
               MAX(data_pagamento) AS ultimo_pagamento
        FROM compra_parcela
        WHERE compra_id = alvo
    ) s
    WHERE c.id = alvo AND s.total > 0;

    RETURN NULL;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_compra_parcela_sincroniza
AFTER INSERT OR UPDATE OR DELETE ON compra_parcela
FOR EACH ROW EXECUTE FUNCTION sincronizar_compra_com_parcelas();

-- ------------------------------------------------------------
-- 2) Parcelamento de despesas
--    Uma despesa parcelada vira N lançamentos em "despesa" (1/3, 2/3, 3/3),
--    ligados a um registro de parcelamento.
-- ------------------------------------------------------------
CREATE TABLE despesa_parcelamento (
    id serial PRIMARY KEY,
    descricao text,
    valor_total numeric(10,2) NOT NULL CHECK (valor_total > 0),
    parcelas integer NOT NULL CHECK (parcelas >= 2),
    criado_por text,
    criado_em timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE despesa
    ADD COLUMN parcelamento_id integer REFERENCES despesa_parcelamento(id) ON DELETE CASCADE,
    ADD COLUMN parcela_numero integer,
    ADD COLUMN parcela_total integer;

ALTER TABLE despesa ADD CONSTRAINT despesa_parcela_ck CHECK (
    (parcelamento_id IS NULL AND parcela_numero IS NULL AND parcela_total IS NULL)
    OR (parcelamento_id IS NOT NULL AND parcela_numero BETWEEN 1 AND parcela_total)
);
CREATE INDEX idx_despesa_parcelamento ON despesa(parcelamento_id);

-- ------------------------------------------------------------
-- 3) Devolução de peças vendidas
--    A peça devolvida volta para o estoque; o valor devolvido à cliente
--    é descontado da receita na data da devolução.
-- ------------------------------------------------------------
CREATE TABLE devolucao (
    id serial PRIMARY KEY,
    venda_id integer NOT NULL REFERENCES venda(id) ON DELETE RESTRICT,
    data date NOT NULL,
    valor_devolvido numeric(10,2) NOT NULL CHECK (valor_devolvido >= 0),
    forma_reembolso text NOT NULL,
    motivo text,
    criado_por text,
    criado_em timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_devolucao_venda ON devolucao(venda_id);
CREATE INDEX idx_devolucao_data ON devolucao(data);

CREATE TABLE devolucao_item (
    id serial PRIMARY KEY,
    devolucao_id integer NOT NULL REFERENCES devolucao(id) ON DELETE CASCADE,
    item_venda_id integer NOT NULL UNIQUE REFERENCES item_venda(id) ON DELETE RESTRICT,  -- cada peça vendida só pode ser devolvida uma vez
    produto_id integer NOT NULL REFERENCES produto(id) ON DELETE RESTRICT,
    valor numeric(10,2) NOT NULL CHECK (valor >= 0)
);
CREATE INDEX idx_devolucao_item_devolucao ON devolucao_item(devolucao_id);
CREATE INDEX idx_devolucao_item_produto ON devolucao_item(produto_id);

COMMIT;
