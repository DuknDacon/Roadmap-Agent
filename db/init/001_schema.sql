CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS savings_products (
    product_id text PRIMARY KEY,
    disclosure_month text NOT NULL,
    company_code text NOT NULL,
    company_name text NOT NULL,
    product_code text NOT NULL,
    product_name text NOT NULL,
    join_member text,
    join_way text,
    maturity_interest text,
    preferential_condition text,
    note text,
    maximum_monthly_payment bigint,
    disclosure_start_date date,
    disclosure_end_date date,
    source_url text,
    raw_data jsonb NOT NULL,
    collected_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (disclosure_month, company_code, product_code)
);

CREATE TABLE IF NOT EXISTS savings_options (
    option_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    product_id text NOT NULL REFERENCES savings_products(product_id) ON DELETE CASCADE,
    saving_term_months integer NOT NULL,
    rate_type text,
    reserve_type text,
    base_rate numeric(7, 4),
    preferential_rate numeric(7, 4),
    raw_data jsonb NOT NULL,
    UNIQUE (product_id, saving_term_months, rate_type, reserve_type)
);

CREATE TABLE IF NOT EXISTS policies (
    policy_id text PRIMARY KEY,
    source_type text NOT NULL CHECK (source_type IN ('youth_policy', 'welfare')),
    policy_name text NOT NULL,
    summary text,
    eligibility_text text,
    benefit_text text,
    application_text text,
    source_url text,
    effective_date date,
    updated_at timestamptz,
    raw_data jsonb NOT NULL,
    collected_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS policies_source_type_idx ON policies (source_type);
CREATE INDEX IF NOT EXISTS policies_raw_data_gin_idx ON policies USING gin (raw_data);

CREATE TABLE IF NOT EXISTS rag_documents (
    document_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source_type text NOT NULL,
    source_id text NOT NULL,
    title text NOT NULL,
    section text NOT NULL DEFAULT '',
    content text NOT NULL,
    source_url text,
    effective_date date,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    embedding vector(1536),
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (source_type, source_id, section)
);

CREATE INDEX IF NOT EXISTS rag_documents_source_idx
    ON rag_documents (source_type, source_id);
