-- ROADMAP_AGENT_DATA.md의 원천 필드 명세를 보존하는 로컬 교체용 계약 스키마.
-- 기존 public 테이블은 에이전트 계산 모델이며, raw 테이블은 원천 응답을 손실 없이 보관한다.

CREATE SCHEMA IF NOT EXISTS raw;

CREATE TABLE IF NOT EXISTS raw.finlife_saving_base (
    dcls_month text NOT NULL,
    fin_co_no text NOT NULL,
    fin_prdt_cd text NOT NULL,
    kor_co_nm text NOT NULL,
    fin_prdt_nm text NOT NULL,
    join_way text,
    mtrt_int text,
    spcl_cnd text,
    join_deny text,
    join_member text,
    etc_note text,
    max_limit bigint,
    dcls_strt_day text,
    dcls_end_day text,
    fin_co_subm_day text,
    source_api text NOT NULL DEFAULT 'finlife_saving_products',
    source_url text,
    collected_at timestamptz NOT NULL DEFAULT now(),
    last_checked_at timestamptz NOT NULL DEFAULT now(),
    raw_data jsonb NOT NULL,
    PRIMARY KEY (dcls_month, fin_co_no, fin_prdt_cd)
);

CREATE TABLE IF NOT EXISTS raw.finlife_saving_option (
    dcls_month text NOT NULL,
    fin_co_no text NOT NULL,
    fin_prdt_cd text NOT NULL,
    intr_rate_type text NOT NULL DEFAULT '',
    intr_rate_type_nm text,
    rsrv_type text NOT NULL DEFAULT '',
    rsrv_type_nm text,
    save_trm text NOT NULL,
    intr_rate numeric(7, 4),
    intr_rate2 numeric(7, 4),
    collected_at timestamptz NOT NULL DEFAULT now(),
    raw_data jsonb NOT NULL,
    PRIMARY KEY (
        dcls_month, fin_co_no, fin_prdt_cd,
        intr_rate_type, rsrv_type, save_trm
    ),
    FOREIGN KEY (dcls_month, fin_co_no, fin_prdt_cd)
        REFERENCES raw.finlife_saving_base (dcls_month, fin_co_no, fin_prdt_cd)
        ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS raw.youth_policy (
    "plcyNo" text PRIMARY KEY,
    "plcyNm" text NOT NULL,
    "plcyKywdNm" text,
    "plcyExplnCn" text,
    "lclsfNm" text,
    "mclsfNm" text,
    "pvsnInstGroupCd" text,
    "plcyPvsnMthdCd" text,
    "plcySprtCn" text,
    "sprvsnInstCd" text,
    "sprvsnInstCdNm" text,
    "operInstCd" text,
    "operInstCdNm" text,
    "aplyYmd" text,
    "aplyPrdSeCd" text,
    "bizPrdSeCd" text,
    "bizPrdBgngYmd" text,
    "bizPrdEndYmd" text,
    "bizPrdEtcCn" text,
    "plcyAplyMthdCn" text,
    "aplyUrlAddr" text,
    "sbmsnDcmntCn" text,
    "srngMthdCn" text,
    "etcMttrCn" text,
    "refUrlAddr1" text,
    "refUrlAddr2" text,
    "sprtTrgtMinAge" text,
    "sprtTrgtMaxAge" text,
    "sprtTrgtAgeLmtYn" text,
    "mrgSttsCd" text,
    "earnCndSeCd" text,
    "earnMinAmt" text,
    "earnMaxAmt" text,
    "earnEtcCn" text,
    "addAplyQlfcCndCn" text,
    "ptcpPrpTrgtCn" text,
    "zipCd" text,
    "plcyMajorCd" text,
    "jobCd" text,
    "schoolCd" text,
    "sbizCd" text,
    "frstRegDt" text,
    "lastMdfcnDt" text,
    "plcyAprvSttsCd" text NOT NULL,
    source_api text NOT NULL DEFAULT 'youthcenter_getPlcy',
    source_url text,
    collected_at timestamptz NOT NULL DEFAULT now(),
    last_checked_at timestamptz NOT NULL DEFAULT now(),
    raw_data jsonb NOT NULL,
    CHECK ("plcyAprvSttsCd" = '0044002')
);

CREATE TABLE IF NOT EXISTS raw.welfare_service_detail (
    "servId" text PRIMARY KEY,
    "servNm" text NOT NULL,
    "servDtlLink" text,
    "jurMnofNm" text,
    "tgtrDtlCn" text,
    "slctCritCn" text,
    "alwServCn" text,
    "wlfareInfoOutlCn" text,
    "crtrYr" text,
    "rprsCtadr" text,
    "sprtCycNm" text,
    "srvPvsnNm" text,
    "applmetList" jsonb,
    "inqplCtadrList" jsonb,
    "inqplHmpgReldList" jsonb,
    "basfrmList" jsonb,
    "baslawList" jsonb,
    source_api text NOT NULL DEFAULT 'central_welfare_detail',
    source_url text,
    collected_at timestamptz NOT NULL DEFAULT now(),
    last_checked_at timestamptz NOT NULL DEFAULT now(),
    raw_data jsonb NOT NULL
);

CREATE TABLE IF NOT EXISTS raw.market_index (
    market_type text NOT NULL CHECK (market_type IN ('stock', 'bond')),
    "basDt" text NOT NULL,
    "idxNm" text NOT NULL,
    "idxCsf" text,
    "clpr" text,
    "vs" text,
    "fltRt" text,
    "mkp" text,
    "hipr" text,
    "lopr" text,
    "trqu" text,
    "trPrc" text,
    "totBnfIdxClpr" text,
    "totBnfIdxVs" text,
    "durt" text,
    "ytm" text,
    collected_at timestamptz NOT NULL DEFAULT now(),
    raw_data jsonb NOT NULL,
    PRIMARY KEY (market_type, "basDt", "idxNm")
);

CREATE TABLE IF NOT EXISTS raw.median_income (
    effective_year integer NOT NULL,
    household_size integer NOT NULL,
    median_monthly_income bigint NOT NULL,
    livelihood_threshold bigint NOT NULL,
    medical_threshold bigint NOT NULL,
    notice_number text NOT NULL,
    announced_on date NOT NULL,
    effective_on date NOT NULL,
    source_url text,
    collected_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (effective_year, household_size)
);

CREATE INDEX IF NOT EXISTS youth_policy_codes_idx
    ON raw.youth_policy ("plcyAprvSttsCd", "jobCd", "earnCndSeCd");
CREATE INDEX IF NOT EXISTS youth_policy_raw_gin_idx
    ON raw.youth_policy USING gin (raw_data);
CREATE INDEX IF NOT EXISTS welfare_detail_raw_gin_idx
    ON raw.welfare_service_detail USING gin (raw_data);
