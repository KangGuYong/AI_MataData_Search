DROP TABLE IF EXISTS meta.metadata_column_term   CASCADE;
DROP TABLE IF EXISTS meta.metadata_business_term CASCADE;
DROP TABLE IF EXISTS meta.metadata_relation      CASCADE;
DROP TABLE IF EXISTS meta.metadata_column_value  CASCADE;
DROP TABLE IF EXISTS meta.metadata_column        CASCADE;
DROP TABLE IF EXISTS meta.metadata_table         CASCADE;
DROP TABLE IF EXISTS meta.datasource             CASCADE;

CREATE TABLE meta.datasource (
    datasource_id   BIGSERIAL PRIMARY KEY,
    name            VARCHAR(100) NOT NULL UNIQUE,
    db_kind         VARCHAR(30)  NOT NULL DEFAULT 'postgresql',
    host            VARCHAR(255),
    port            INTEGER,
    database_name   VARCHAR(128),
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE meta.metadata_table (
    table_id        BIGSERIAL PRIMARY KEY,
    datasource_id   BIGINT NOT NULL REFERENCES meta.datasource(datasource_id) ON DELETE CASCADE,
    schema_name     VARCHAR(128) NOT NULL,
    table_name      VARCHAR(128) NOT NULL,
    table_type      VARCHAR(30)  NOT NULL DEFAULT 'TABLE',
    table_comment   TEXT,
    business_name   VARCHAR(255),
    business_desc   TEXT,
    row_count_est   BIGINT,
    search_text     TEXT,
    embedding       vector(1024),
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_metadata_table UNIQUE (datasource_id, schema_name, table_name)
);

CREATE TABLE meta.metadata_column (
    column_id        BIGSERIAL PRIMARY KEY,
    table_id         BIGINT NOT NULL REFERENCES meta.metadata_table(table_id) ON DELETE CASCADE,
    column_name      VARCHAR(128) NOT NULL,
    ordinal_position INTEGER,
    data_type        VARCHAR(100),
    is_nullable      BOOLEAN,
    is_primary_key   BOOLEAN NOT NULL DEFAULT FALSE,
    is_foreign_key   BOOLEAN NOT NULL DEFAULT FALSE,
    column_comment   TEXT,
    business_name    VARCHAR(255),
    business_desc    TEXT,
    distinct_count   BIGINT,
    null_ratio       NUMERIC(5,4),
    min_value        TEXT,
    max_value        TEXT,
    sample_values    TEXT[],
    search_text      TEXT,
    embedding        vector(1024),
    is_sensitive     BOOLEAN NOT NULL DEFAULT FALSE,
    is_active        BOOLEAN NOT NULL DEFAULT TRUE,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_metadata_column UNIQUE (table_id, column_name)
);

CREATE TABLE meta.metadata_column_value (
    value_id      BIGSERIAL PRIMARY KEY,
    column_id     BIGINT NOT NULL REFERENCES meta.metadata_column(column_id) ON DELETE CASCADE,
    value_text    TEXT   NOT NULL,
    value_freq    BIGINT,
    embedding     vector(1024),
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_column_value UNIQUE (column_id, value_text)
);

CREATE TABLE meta.metadata_relation (
    relation_id     BIGSERIAL PRIMARY KEY,
    from_table_id   BIGINT NOT NULL REFERENCES meta.metadata_table(table_id)   ON DELETE CASCADE,
    from_column_id  BIGINT NOT NULL REFERENCES meta.metadata_column(column_id) ON DELETE CASCADE,
    to_table_id     BIGINT NOT NULL REFERENCES meta.metadata_table(table_id)   ON DELETE CASCADE,
    to_column_id    BIGINT NOT NULL REFERENCES meta.metadata_column(column_id) ON DELETE CASCADE,
    relation_type   VARCHAR(30) NOT NULL DEFAULT 'FOREIGN_KEY',
    relation_name   VARCHAR(255),
    description     TEXT,
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_metadata_relation UNIQUE (from_table_id, from_column_id, to_table_id, to_column_id)
);

CREATE TABLE meta.metadata_business_term (
    term_id      BIGSERIAL PRIMARY KEY,
    term         VARCHAR(255) NOT NULL UNIQUE,
    term_type    VARCHAR(50),
    description  TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE meta.metadata_column_term (
    column_id  BIGINT NOT NULL REFERENCES meta.metadata_column(column_id) ON DELETE CASCADE,
    term_id    BIGINT NOT NULL REFERENCES meta.metadata_business_term(term_id) ON DELETE CASCADE,
    weight     NUMERIC(5,2) NOT NULL DEFAULT 1.0,
    source     VARCHAR(20)  NOT NULL DEFAULT 'llm',
    PRIMARY KEY (column_id, term_id)
);

CREATE INDEX ix_col_search_trgm ON meta.metadata_column       USING GIN (search_text gin_trgm_ops);
CREATE INDEX ix_tbl_search_trgm ON meta.metadata_table        USING GIN (search_text gin_trgm_ops);
CREATE INDEX ix_val_text_trgm   ON meta.metadata_column_value USING GIN (value_text  gin_trgm_ops);

CREATE INDEX ix_col_emb ON meta.metadata_column       USING hnsw (embedding vector_cosine_ops);
CREATE INDEX ix_tbl_emb ON meta.metadata_table        USING hnsw (embedding vector_cosine_ops);
CREATE INDEX ix_val_emb ON meta.metadata_column_value USING hnsw (embedding vector_cosine_ops);

CREATE INDEX ix_col_table ON meta.metadata_column(table_id);
CREATE INDEX ix_rel_from  ON meta.metadata_relation(from_table_id);
CREATE INDEX ix_rel_to    ON meta.metadata_relation(to_table_id);
