from __future__ import annotations

import os
import re
from collections.abc import Callable, Mapping
from datetime import date, datetime
from typing import Any

from .domain import RoadmapRequest
from .ports import PolicyBenefit, SavingsProduct


ConnectionFactory = Callable[[], Any]
MONEY_RE = re.compile(
    r"(?:매?월|납입한도[^\n\r]*?월)[^\d\n\r]{0,20}([\d,.]+)\s*(만)?원"
)
MONTH_RE = re.compile(r"(\d+)\s*개월")
YEAR_RE = re.compile(r"(\d+)\s*년")
MAX_SUPPORT_RE = re.compile(r"(?:최대|지원금[^\n\r]{0,20})\s*([\d,.]+)\s*(만)?원")
PERCENT_RE = re.compile(r"일반형\s*([\d.]+)%")
PREFERENTIAL_PERCENT_RE = re.compile(r"우대형\s*([\d.]+)%")
DATE_RANGE_RE = re.compile(r"(\d{8})\s*~\s*(\d{8})")
AGE_RANGE_RE = re.compile(r"만?\s*(\d+)세\s*[~～-]\s*(\d+)세")
FINLIFE_SOURCE_URL = "https://finlife.fss.or.kr/finlife/main/contents.do?menuNo=700029"


def _number(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(float(str(value).replace(",", "").strip()))
    except ValueError:
        return None


def _won(match: re.Match[str]) -> int:
    amount = float(match.group(1).replace(",", ""))
    return round(amount * (10_000 if match.group(2) else 1))


def _monthly_limit(text: str) -> int | None:
    match = MONEY_RE.search(text)
    return _won(match) if match else None


def _maturity_months(text: str, horizon: int) -> int | None:
    values = [int(value) for value in MONTH_RE.findall(text)]
    values.extend(int(value) * 12 for value in YEAR_RE.findall(text))
    values = sorted({value for value in values if value > 0})
    if not values:
        return None
    eligible = [value for value in values if value <= horizon]
    return max(eligible) if eligible else min(values)


def _estimated_support(text: str, monthly: int, months: int) -> int | None:
    if "1:1" in text or "저축한 금액만큼" in text or "동일한 금액" in text:
        return monthly * months
    percent = PERCENT_RE.search(text)
    if percent:
        return round(monthly * months * float(percent.group(1)) / 100)
    maximum = MAX_SUPPORT_RE.search(text)
    return _won(maximum) if maximum else None


def _support_rate(text: str) -> float | None:
    if "1:1" in text or "저축한 금액만큼" in text or "동일한 금액" in text:
        return 1.0
    percent = PERCENT_RE.search(text)
    return float(percent.group(1)) / 100 if percent else None


def _preferential_support_rate(text: str) -> float | None:
    percent = PREFERENTIAL_PERCENT_RE.search(text)
    return float(percent.group(1)) / 100 if percent else None


def _date_value(value: Any) -> str:
    text = str(value or "").strip()
    if len(text) >= 8 and text[:8].isdigit():
        return f"{text[:4]}-{text[4:6]}-{text[6:8]}"
    return ""


def _application_open(value: Any, as_of: date) -> bool | None:
    match = DATE_RANGE_RE.search(str(value or ""))
    if not match:
        return None
    start = datetime.strptime(match.group(1), "%Y%m%d").date()
    end = datetime.strptime(match.group(2), "%Y%m%d").date()
    return start <= as_of <= end


def map_savings_row(row: Mapping[str, Any]) -> SavingsProduct | None:
    term = _number(row.get("save_trm"))
    base_rate = row.get("intr_rate")
    preferred_rate = row.get("intr_rate2")
    if term is None or base_rate is None:
        return None
    key = ":".join(
        str(row.get(name) or "")
        for name in ("dcls_month", "fin_co_no", "fin_prdt_cd", "intr_rate_type", "rsrv_type", "save_trm")
    )
    return SavingsProduct(
        product_id=key,
        company_name=str(row.get("kor_co_nm") or ""),
        product_name=str(row.get("fin_prdt_nm") or ""),
        annual_base_rate=float(base_rate) / 100,
        annual_preferential_rate=float(preferred_rate if preferred_rate is not None else base_rate) / 100,
        maximum_monthly_payment=_monthly_limit(str(row.get("etc_note") or "")),
        term_months=term,
        tax_rate=0.154,
        source_url=str(row.get("source_url") or FINLIFE_SOURCE_URL),
        effective_date=_date_value(row.get("dcls_strt_day")),
        savings_type_name=str(
            row.get("rsrv_type_nm")
            or {"F": "자유적립식", "S": "정액적립식"}.get(str(row.get("rsrv_type") or ""), "")
        ),
        interest_type_name=str(
            row.get("intr_rate_type_nm")
            or {"S": "단리", "M": "복리"}.get(str(row.get("intr_rate_type") or ""), "")
        ),
    )


def map_youth_policy_row(
    row: Mapping[str, Any], request: RoadmapRequest, *, as_of: date
) -> PolicyBenefit | None:
    text = "\n".join(
        str(row.get(name) or "")
        for name in (
            "plcyExplnCn",
            "plcySprtCn",
            "earnEtcCn",
            "addAplyQlfcCndCn",
            "bizPrdEtcCn",
            "etcMttrCn",
        )
    )
    monthly = _monthly_limit(text)
    months = _maturity_months(text, request.horizon_months)
    if monthly is None or months is None:
        return None
    support = _estimated_support(text, monthly, months)
    if support is None:
        return None

    reasons: list[str] = []
    eligible = True
    min_age = _number(row.get("sprtTrgtMinAge")) or 0
    max_age = _number(row.get("sprtTrgtMaxAge")) or 0
    if min_age or max_age:
        if request.age is None:
            eligible = False
            reasons.append("나이 확인 필요")
        elif request.age < min_age or (max_age and request.age > max_age):
            eligible = False
            reasons.append(f"연령조건 {min_age}~{max_age}세 불충족")
        else:
            reasons.append(f"연령조건 {min_age}~{max_age}세 충족")

    max_income = _number(row.get("earnMaxAmt")) or 0
    if max_income:
        if request.annual_income is None:
            eligible = False
            reasons.append("연소득 확인 필요")
        elif request.annual_income > max_income * 10_000:
            eligible = False
            reasons.append("연소득 상한 초과")
        else:
            reasons.append("연소득 기본조건 충족")

    zip_codes = {value.strip() for value in str(row.get("zipCd") or "").split(",") if value.strip()}
    if zip_codes and request.region_code:
        district = request.region_code.split(":")[-1]
        if district not in zip_codes:
            eligible = False
            reasons.append("지역조건 불충족")

    application_open = _application_open(row.get("aplyYmd"), as_of)
    if not application_open:
        reasons.append("현재 확인된 신청기간은 종료됨")
    income_text = str(row.get("earnEtcCn") or "")
    if "중위소득" in income_text:
        reasons.append("가구 중위소득 충족 여부 추가 확인 필요")
    if (
        "금융소득" in text
        or "금융소득" in str(row.get("plcyAplyMthdCn") or "")
        or "미래적금" in str(row.get("plcyNm") or "")
    ):
        reasons.append("금융소득종합과세 대상 여부 추가 확인 필요")
    if request.is_sme_employee and "우대형" in text:
        reasons.append("중소기업 재직자로 우대형 가능성이 있으나 세부 자격 확인 필요")
    if "금리" in text and ("자율 결정" in text or "자율결정" in text):
        reasons.append("취급 금융기관 금리 추가 정보 필요(현재 예상액에는 은행이자 미포함)")
    if row.get("addAplyQlfcCndCn") or row.get("ptcpPrpTrgtCn"):
        reasons.append("추가 자격조건은 운영기관 확인 필요")

    return PolicyBenefit(
        policy_id=str(row.get("plcyNo") or ""),
        name=str(row.get("plcyNm") or ""),
        eligible=eligible,
        monthly_limit=monthly,
        estimated_support=support,
        maturity_months=months,
        source_url=str(row.get("source_url") or row.get("refUrlAddr1") or row.get("refUrlAddr2") or ""),
        effective_date=_date_value(row.get("lastMdfcnDt")) or str(as_of),
        reason="; ".join(reasons) or "명시된 기본조건 충족",
        application_open=application_open,
        support_rate=_support_rate(text),
        preferential_support_rate=_preferential_support_rate(text),
    )


def map_welfare_policy_row(
    row: Mapping[str, Any], request: RoadmapRequest, *, as_of: date
) -> PolicyBenefit | None:
    target = str(row.get("tgtrDtlCn") or "")
    benefit = str(row.get("alwServCn") or "")
    monthly = _monthly_limit(benefit)
    months = _maturity_months(benefit, request.horizon_months)
    if monthly is None or months is None:
        return None
    support = _estimated_support(benefit, monthly, months)
    if support is None:
        return None

    eligible = True
    reasons: list[str] = []
    age_match = AGE_RANGE_RE.search(target)
    if age_match:
        low, high = map(int, age_match.groups())
        if request.age is None:
            eligible = False
            reasons.append("나이 확인 필요")
        elif not low <= request.age <= high:
            eligible = False
            reasons.append(f"연령조건 {low}~{high}세 불충족")
        else:
            reasons.append(f"연령조건 {low}~{high}세 충족")
    if "중위소득" in target:
        eligible = False
        reasons.append("가구소득 인정액·중위소득 비율 확인 필요")
    if any(term in target for term in ("북향민", "농업인", "어업인", "무주택")):
        eligible = False
        reasons.append("대상자 특수조건 확인 필요")

    return PolicyBenefit(
        policy_id=str(row.get("servId") or ""),
        name=str(row.get("servNm") or ""),
        eligible=eligible,
        monthly_limit=monthly,
        estimated_support=support,
        maturity_months=months,
        source_url=str(row.get("source_url") or row.get("servDtlLink") or ""),
        effective_date=f"{row.get('crtrYr') or as_of.year}-01-01",
        reason="; ".join(reasons) or "명시된 기본조건 충족",
        application_open=None,
        support_rate=_support_rate(benefit),
        preferential_support_rate=_preferential_support_rate(benefit),
    )


class PostgresSavingsProductRepository:
    def __init__(self, connection_factory: ConnectionFactory):
        self.connection_factory = connection_factory

    def find_candidates(self, request: RoadmapRequest) -> list[SavingsProduct]:
        query = """
            SELECT b.dcls_month, b.fin_co_no, b.fin_prdt_cd, b.kor_co_nm,
                   b.fin_prdt_nm, b.etc_note, b.dcls_strt_day, b.source_url,
                   o.intr_rate_type, o.rsrv_type, o.save_trm, o.intr_rate, o.intr_rate2
              FROM raw.finlife_saving_base b
              JOIN raw.finlife_saving_option o
                USING (dcls_month, fin_co_no, fin_prdt_cd)
             WHERE o.intr_rate IS NOT NULL
               AND o.intr_rate_type = 'S'
               AND o.save_trm ~ '^[0-9]+$'
               AND o.save_trm::integer <= %s
             ORDER BY o.save_trm::integer DESC, o.intr_rate2 DESC NULLS LAST,
                      o.intr_rate DESC, b.fin_prdt_nm
        """
        with self.connection_factory() as connection:
            with connection.cursor() as cursor:
                cursor.execute(query, (request.horizon_months,))
                products = [map_savings_row(row) for row in cursor.fetchall()]
        return [product for product in products if product is not None]


class PostgresPolicyRepository:
    def __init__(self, connection_factory: ConnectionFactory, *, as_of: date | None = None):
        self.connection_factory = connection_factory
        self.as_of = as_of or date.today()

    def find_candidates(self, request: RoadmapRequest) -> list[PolicyBenefit]:
        with self.connection_factory() as connection:
            with connection.cursor() as cursor:
                cursor.execute('SELECT * FROM raw.youth_policy WHERE "plcyAprvSttsCd" = %s', ("0044002",))
                youth = [map_youth_policy_row(row, request, as_of=self.as_of) for row in cursor.fetchall()]
                cursor.execute('SELECT * FROM raw.welfare_service_detail')
                welfare = [map_welfare_policy_row(row, request, as_of=self.as_of) for row in cursor.fetchall()]
        candidates = [item for item in [*youth, *welfare] if item is not None]
        unique: dict[str, PolicyBenefit] = {}
        for item in candidates:
            current = unique.get(item.name)
            if current is None or (item.eligible, item.estimated_support) > (
                current.eligible,
                current.estimated_support,
            ):
                unique[item.name] = item
        return sorted(unique.values(), key=lambda item: (not item.eligible, -item.estimated_support, item.name))


def postgres_connection_factory_from_env() -> ConnectionFactory:
    """환경변수로 psycopg 연결 팩토리를 만든다. 비밀번호는 로그에 출력하지 않는다."""
    required = ("POSTGRES_DB", "POSTGRES_USER", "POSTGRES_PASSWORD")
    missing = [name for name in required if not os.environ.get(name)]
    if missing:
        raise RuntimeError("PostgreSQL 환경변수 누락: " + ", ".join(missing))

    def connect() -> Any:
        try:
            import psycopg
            from psycopg.rows import dict_row
        except ImportError as exc:
            raise RuntimeError("PostgreSQL 연결에는 psycopg 패키지가 필요합니다.") from exc
        return psycopg.connect(
            dbname=os.environ["POSTGRES_DB"],
            user=os.environ["POSTGRES_USER"],
            password=os.environ["POSTGRES_PASSWORD"],
            host=os.environ.get("POSTGRES_HOST", "localhost"),
            port=os.environ.get("POSTGRES_PORT", "5432"),
            row_factory=dict_row,
        )

    return connect
