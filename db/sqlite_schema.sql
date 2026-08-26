PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

-- 성필님이 제공할 공용 SQLite가 따라야 하는 기능 2 정형 데이터 계약.
-- PostgreSQL raw schema의 컬럼을 보존하되 SQLite에는 schema namespace가 없어
-- 테이블을 최상위에 둔다. raw_data JSON은 TEXT(JSON 문자열)로 저장한다.
CREATE TABLE IF NOT EXISTS finlife_saving_base (
    dcls_month TEXT NOT NULL,
    fin_co_no TEXT NOT NULL,
    fin_prdt_cd TEXT NOT NULL,
    kor_co_nm TEXT NOT NULL,
    fin_prdt_nm TEXT NOT NULL,
    join_way TEXT, mtrt_int TEXT, spcl_cnd TEXT, join_deny TEXT,
    join_member TEXT, etc_note TEXT, max_limit INTEGER,
    dcls_strt_day TEXT, dcls_end_day TEXT, fin_co_subm_day TEXT,
    source_api TEXT DEFAULT 'finlife_saving_products', source_url TEXT,
    collected_at TEXT, last_checked_at TEXT, raw_data TEXT NOT NULL DEFAULT '{}',
    PRIMARY KEY (dcls_month, fin_co_no, fin_prdt_cd)
);

CREATE TABLE IF NOT EXISTS finlife_saving_option (
    dcls_month TEXT NOT NULL,
    fin_co_no TEXT NOT NULL,
    fin_prdt_cd TEXT NOT NULL,
    intr_rate_type TEXT NOT NULL DEFAULT '', intr_rate_type_nm TEXT,
    rsrv_type TEXT NOT NULL DEFAULT '', rsrv_type_nm TEXT,
    save_trm TEXT NOT NULL, intr_rate REAL, intr_rate2 REAL,
    collected_at TEXT, raw_data TEXT NOT NULL DEFAULT '{}',
    PRIMARY KEY (dcls_month, fin_co_no, fin_prdt_cd, intr_rate_type, rsrv_type, save_trm),
    FOREIGN KEY (dcls_month, fin_co_no, fin_prdt_cd)
      REFERENCES finlife_saving_base (dcls_month, fin_co_no, fin_prdt_cd) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS youth_policy (
    plcyNo TEXT PRIMARY KEY, plcyNm TEXT NOT NULL, plcyKywdNm TEXT,
    plcyExplnCn TEXT, lclsfNm TEXT, mclsfNm TEXT, pvsnInstGroupCd TEXT,
    plcyPvsnMthdCd TEXT, plcySprtCn TEXT, sprvsnInstCd TEXT,
    sprvsnInstCdNm TEXT, operInstCd TEXT, operInstCdNm TEXT, aplyYmd TEXT,
    aplyPrdSeCd TEXT, bizPrdSeCd TEXT, bizPrdBgngYmd TEXT, bizPrdEndYmd TEXT,
    bizPrdEtcCn TEXT, plcyAplyMthdCn TEXT, aplyUrlAddr TEXT,
    sbmsnDcmntCn TEXT, srngMthdCn TEXT, etcMttrCn TEXT, refUrlAddr1 TEXT,
    refUrlAddr2 TEXT, sprtTrgtMinAge TEXT, sprtTrgtMaxAge TEXT,
    sprtTrgtAgeLmtYn TEXT, mrgSttsCd TEXT, earnCndSeCd TEXT,
    earnMinAmt TEXT, earnMaxAmt TEXT, earnEtcCn TEXT, addAplyQlfcCndCn TEXT,
    ptcpPrpTrgtCn TEXT, zipCd TEXT, plcyMajorCd TEXT, jobCd TEXT,
    schoolCd TEXT, sbizCd TEXT, frstRegDt TEXT, lastMdfcnDt TEXT,
    plcyAprvSttsCd TEXT NOT NULL, source_api TEXT DEFAULT 'youthcenter_getPlcy',
    source_url TEXT, collected_at TEXT, last_checked_at TEXT,
    raw_data TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS youth_policy_codes_idx
ON youth_policy (plcyAprvSttsCd, jobCd, earnCndSeCd);

CREATE TABLE IF NOT EXISTS welfare_service_detail (
    servId TEXT PRIMARY KEY, servNm TEXT NOT NULL, servDtlLink TEXT,
    jurMnofNm TEXT, tgtrDtlCn TEXT, slctCritCn TEXT, alwServCn TEXT,
    wlfareInfoOutlCn TEXT, crtrYr TEXT, rprsCtadr TEXT, sprtCycNm TEXT,
    srvPvsnNm TEXT, applmetList TEXT, inqplCtadrList TEXT,
    inqplHmpgReldList TEXT, basfrmList TEXT, baslawList TEXT,
    source_api TEXT DEFAULT 'central_welfare_detail', source_url TEXT,
    collected_at TEXT, last_checked_at TEXT, raw_data TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS market_index (
    market_type TEXT NOT NULL, basDt TEXT NOT NULL, idxNm TEXT NOT NULL,
    idxCsf TEXT, clpr TEXT, vs TEXT, fltRt TEXT, mkp TEXT, hipr TEXT,
    lopr TEXT, trqu TEXT, trPrc TEXT, totBnfIdxClpr TEXT, totBnfIdxVs TEXT,
    durt TEXT, ytm TEXT, collected_at TEXT, raw_data TEXT NOT NULL DEFAULT '{}',
    PRIMARY KEY (market_type, basDt, idxNm)
);

CREATE TABLE IF NOT EXISTS median_income (
    effective_year INTEGER NOT NULL, household_size INTEGER NOT NULL,
    median_monthly_income INTEGER NOT NULL, livelihood_threshold INTEGER NOT NULL,
    medical_threshold INTEGER NOT NULL, notice_number TEXT NOT NULL,
    announced_on TEXT NOT NULL, effective_on TEXT NOT NULL,
    source_url TEXT, collected_at TEXT,
    PRIMARY KEY (effective_year, household_size)
);
