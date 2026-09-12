-- ============================================================
-- ENNE Brechó — Refinamento do modelo
-- Rodar no SQL Editor do Supabase, de uma vez (é tudo uma transação)
-- ============================================================

BEGIN;

-- ------------------------------------------------------------
-- 1) Catálogos novos
-- ------------------------------------------------------------

CREATE TABLE tipo_peca (
    id serial PRIMARY KEY,
    nome text NOT NULL UNIQUE
);

CREATE TABLE tipo_compra (
    id serial PRIMARY KEY,
    nome text NOT NULL UNIQUE,
    prazo_dias integer NOT NULL DEFAULT 0,       -- 0 = pago na hora (à vista)
    requer_fornecedora boolean NOT NULL DEFAULT true
);

CREATE TABLE plano_contas (
    id serial PRIMARY KEY,
    tipo text NOT NULL CHECK (tipo IN ('despesa', 'receita')),
    nome text NOT NULL,
    UNIQUE (tipo, nome)
);

-- Sementes: tipos de peça comuns de brechó
INSERT INTO tipo_peca (nome) VALUES
    ('Camisas'), ('Blusas'), ('Vestidos'), ('Calças'), ('Saias'),
    ('Shorts e Bermudas'), ('Jaquetas e Casacos'), ('Moletons'),
    ('Conjuntos'), ('Macacões'), ('Biquínis e Maiôs'), ('Bolsas'),
    ('Calçados'), ('Acessórios'), ('Roupas íntimas'), ('Outros')
ON CONFLICT (nome) DO NOTHING;

-- Sementes: tipos de compra
INSERT INTO tipo_compra (nome, prazo_dias, requer_fornecedora) VALUES
    ('Fornecedora (compra a prazo)', 30, true),
    ('Bazar', 0, false),
    ('Outros', 0, false)
ON CONFLICT (nome) DO NOTHING;

-- Sementes: plano de contas
INSERT INTO plano_contas (tipo, nome) VALUES
    ('despesa', 'Aluguel'),
    ('despesa', 'Contas (água/luz/internet)'),
    ('despesa', 'Embalagens e sacolas'),
    ('despesa', 'Marketing'),
    ('despesa', 'Manutenção'),
    ('despesa', 'Impostos'),
    ('despesa', 'Salários e comissões'),
    ('despesa', 'Transporte'),
    ('despesa', 'Outros'),
    ('receita', 'Venda de peças'),
    ('receita', 'Frete cobrado'),
    ('receita', 'Outras receitas')
ON CONFLICT (tipo, nome) DO NOTHING;

-- ------------------------------------------------------------
-- 2) produto: categoria (texto livre) -> tipo_peca_id (catálogo)
-- ------------------------------------------------------------

ALTER TABLE produto ADD COLUMN tipo_peca_id integer REFERENCES tipo_peca(id);

-- garante que nenhum valor já usado em categoria fica órfão,
-- mesmo que não esteja nas sementes acima
INSERT INTO tipo_peca (nome)
SELECT DISTINCT categoria FROM produto
WHERE categoria IS NOT NULL
  AND NOT EXISTS (SELECT 1 FROM tipo_peca tp WHERE lower(tp.nome) = lower(produto.categoria));

UPDATE produto p
SET tipo_peca_id = tp.id
FROM tipo_peca tp
WHERE lower(tp.nome) = lower(p.categoria);

ALTER TABLE produto DROP COLUMN categoria;
CREATE INDEX idx_produto_tipo_peca ON produto(tipo_peca_id);

-- ------------------------------------------------------------
-- 3) compra: tipo_compra, fornecedora opcional, vencimento flexível
-- ------------------------------------------------------------

ALTER TABLE compra ADD COLUMN tipo_compra_id integer REFERENCES tipo_compra(id);

UPDATE compra
SET tipo_compra_id = (SELECT id FROM tipo_compra WHERE nome = 'Fornecedora (compra a prazo)')
WHERE tipo_compra_id IS NULL;

ALTER TABLE compra ALTER COLUMN tipo_compra_id SET NOT NULL;
ALTER TABLE compra ALTER COLUMN fornecedora_id DROP NOT NULL;

-- data_vencimento era uma coluna calculada (sempre +30 dias); agora o
-- prazo varia por tipo de compra, então vira uma coluna normal que o
-- app preenche (mantém os valores já calculados até aqui)
ALTER TABLE compra ALTER COLUMN data_vencimento DROP EXPRESSION;

CREATE INDEX idx_compra_tipo_compra ON compra(tipo_compra_id);

-- ------------------------------------------------------------
-- 4) despesa: categoria (texto livre) -> plano_conta_id (catálogo)
-- ------------------------------------------------------------

ALTER TABLE despesa ADD COLUMN plano_conta_id integer REFERENCES plano_contas(id);

INSERT INTO plano_contas (tipo, nome)
SELECT DISTINCT 'despesa', categoria FROM despesa
WHERE NOT EXISTS (
    SELECT 1 FROM plano_contas pc WHERE pc.tipo = 'despesa' AND lower(pc.nome) = lower(despesa.categoria)
);

UPDATE despesa d
SET plano_conta_id = pc.id
FROM plano_contas pc
WHERE pc.tipo = 'despesa' AND lower(pc.nome) = lower(d.categoria);

ALTER TABLE despesa ALTER COLUMN plano_conta_id SET NOT NULL;
ALTER TABLE despesa DROP COLUMN categoria;
CREATE INDEX idx_despesa_plano_conta ON despesa(plano_conta_id);

-- ------------------------------------------------------------
-- 5) venda: desconto + classificação opcional de receita
-- ------------------------------------------------------------

ALTER TABLE venda ADD COLUMN desconto numeric(10,2) NOT NULL DEFAULT 0 CHECK (desconto >= 0);
ALTER TABLE venda ADD COLUMN plano_conta_id integer REFERENCES plano_contas(id);

UPDATE venda
SET plano_conta_id = (SELECT id FROM plano_contas WHERE tipo = 'receita' AND nome = 'Venda de peças')
WHERE plano_conta_id IS NULL;

CREATE INDEX idx_venda_plano_conta ON venda(plano_conta_id);

COMMIT;
