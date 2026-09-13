-- ============================================================
-- ENNE Brechó — Avaliação de peças (proposta antes da compra)
-- Rodar no SQL Editor do Supabase, de uma vez
-- ============================================================

BEGIN;

CREATE TABLE avaliacao (
    id serial PRIMARY KEY,
    fornecedora_id integer NOT NULL REFERENCES fornecedora(id) ON DELETE RESTRICT,
    data_avaliacao date NOT NULL,
    status text NOT NULL DEFAULT 'pendente' CHECK (status IN ('pendente', 'aceita', 'recusada')),
    compra_id integer REFERENCES compra(id),
    criado_em timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_avaliacao_fornecedora ON avaliacao(fornecedora_id);
CREATE INDEX idx_avaliacao_status ON avaliacao(status);

CREATE TABLE avaliacao_item (
    id serial PRIMARY KEY,
    avaliacao_id integer NOT NULL REFERENCES avaliacao(id) ON DELETE CASCADE,
    descricao text NOT NULL,
    tipo_peca_id integer REFERENCES tipo_peca(id),
    tamanho text,
    aprovada boolean NOT NULL DEFAULT false,
    valor_proposto numeric(10,2) CHECK (valor_proposto IS NULL OR valor_proposto >= 0),
    observacao text,
    CHECK ((aprovada AND valor_proposto IS NOT NULL) OR (NOT aprovada))
);
CREATE INDEX idx_avaliacao_item_avaliacao ON avaliacao_item(avaliacao_id);

COMMIT;
