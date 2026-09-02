from __future__ import annotations

import os
import re
import sqlite3
from collections.abc import Callable, Mapping
from datetime import date, datetime
from pathlib import Path
from typing import Any

from .domain import RoadmapRequest
from .dynamic_gates import DynamicGateRegistry
from .policy_qualification import effective_household_monthly_income, median_income_limit
from .policy_rules import PolicyRuleCatalog
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


_STIPEND_KEYWORDS = (
    "참여수당",
    "구직촉진수당",
    "취업활동비용",
    "근로수당",
    "실습수당",
    "활동수당",
    "훈련비",
)


def _is_stipend_program(text: str) -> bool:
    # "월 234만원 참여수당 지급" 같은 취업·훈련 참여수당형 정책은 저축액에
    # 비례해 정부가 보태주는 적립형 상품이 아니라, 일하거나 훈련에 참여한
    # 대가로 받는 급여성 지원이다. estimated_support가 이런 텍스트에서
    # "최대 234만원" 패턴으로 잘못 추출되면 로드맵 시나리오가 이를
    # "정부기여금"인 것처럼 안내하게 되므로, 저축형 정책상품 후보에서
    # 제외할 수 있도록 별도로 표시해둔다.
    return any(keyword in text for keyword in _STIPEND_KEYWORDS)


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


def _apply_dynamic_gates(
    policy_id: str,
    gate_registry: DynamicGateRegistry | None,
    request: RoadmapRequest,
    eligible: bool,
    missing_fields: list[str],
    reasons: list[str],
) -> bool:
    """DynamicGateRegistry가 발견한 게이트(4개 하드코딩 필드를 넘어서는 예/아니오
    자격조건)를 missing_fields/reasons/eligible에 접어넣는다. LLM은 이 gate.question을
    만들었을 뿐이고, 답변을 보고 eligible을 계산하는 건 여기 이 코드다.

    게이트가 상품마다 여러 개(많으면 9개 이상)라, 만족/대기 중인 게이트마다
    질문 문구를 통째로 reasons에 쌓으면 챗봇 답변이 문장 나열로 도배된다
    (실사용자 피드백으로 발견). 실제로 사용자가 알아야 할 건 "몇 개나
    확인됐는지"와 "왜 탈락했는지"뿐이라, 만족/대기는 개수로만 요약하고
    탈락 사유 하나만 원문 그대로 남긴다.
    """
    if gate_registry is None:
        return eligible
    satisfied = 0
    pending = 0
    for gate in gate_registry.gates_for(policy_id):
        composite = DynamicGateRegistry.composite_id(policy_id, gate.gate_id)
        answer = request.dynamic_gate_answers.get(composite)
        if answer is None:
            missing_fields.append(composite)
            pending += 1
        elif answer is False:
            eligible = False
            reasons.append(f"{gate.question} — 미충족")
            # 이 게이트에서 이미 탈락이 확정됐다 — 같은 상품에 게이트가 더
            # 있어도 더 물어보지 않는다(호출부도 eligible이었을 때만 이
            # 함수를 부르므로, 여기 도달했다는 건 이번 게이트가 이 상품의
            # 첫 탈락 사유라는 뜻).
            break
        else:
            satisfied += 1
    if satisfied:
        reasons.append(f"동적 자격조건 {satisfied}건 충족")
    if pending:
        reasons.append(f"동적 자격조건 {pending}건 확인 필요")
    return eligible


def map_youth_policy_row(
    row: Mapping[str, Any],
    request: RoadmapRequest,
    *,
    as_of: date,
    gate_registry: DynamicGateRegistry | None = None,
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
    missing_fields: list[str] = []
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

    # 지역조건은 나이와 마찬가지로 온보딩만으로 항상 판정 가능한 하드 조건이라
    # 소득캡 확인보다 먼저 본다 — 아래 "확인 필요" 게이팅(if eligible: ...)이
    # 지역 탈락 여부까지 반영하려면 순서가 이래야 한다.
    zip_codes = {value.strip() for value in str(row.get("zipCd") or "").split(",") if value.strip()}
    if zip_codes and request.region_code:
        district = request.region_code.split(":")[-1]
        if district not in zip_codes:
            eligible = False
            reasons.append("지역조건 불충족")

    # 나이·지역처럼 온보딩만으로 항상 판정되는 조건이 이미 이 상품을 탈락시켰으면,
    # 그 뒤로는 사용자에게 새 정보를 더 캐묻지 않는다 — 이미 못 받는 상품에
    # 가구소득·금융소득종합과세 여부까지 계속 물어보면 불필요한 질문만 늘어난다.
    # (이미 답변된 값으로 하는 부가 판정·안내 문구는 계속 반영한다 — 어차피
    # 질문이 아니라 정보 전달이라 여기서 막을 이유가 없다.)
    max_income = _number(row.get("earnMaxAmt")) or 0
    taxable_income = request.previous_annual_income
    if max_income:
        if taxable_income is None:
            if eligible:
                missing_fields.append("previous_annual_income")
                reasons.append("직전년도 과세소득 확인 필요")
        elif taxable_income > max_income * 10_000:
            eligible = False
            reasons.append("직전년도 과세소득 상한 초과")
        else:
            reasons.append("직전년도 과세소득 기본조건 충족")

    if (
        request.previous_annual_income is not None
        and request.current_annual_income is not None
    ):
        change = abs(request.current_annual_income - request.previous_annual_income)
        if change / max(request.previous_annual_income, 1) >= 0.2:
            reasons.append("현재 예상소득 변동폭이 커 기준연도별 자격 재확인 필요")

    application_open = _application_open(row.get("aplyYmd"), as_of)
    if not application_open:
        reasons.append("현재 확인된 신청기간은 종료됨")
    income_text = str(row.get("earnEtcCn") or "")
    if "중위소득" in income_text:
        household_income = effective_household_monthly_income(request)
        if household_income is None:
            if eligible:
                missing_fields.append("household_monthly_income")
                reasons.append("가구 중위소득 판정을 위한 월소득 입력 필요")
        elif request.household_size is None:
            if eligible:
                missing_fields.append("household_size")
                reasons.append("가구 중위소득 판정을 위한 가구원 수 입력 필요")
        else:
            income_limit = median_income_limit(income_text, as_of.year, request.household_size)
            if income_limit is None:
                reasons.append("가구 중위소득 기준연도 또는 비율 확인 필요")
            else:
                ratio, limit = income_limit
                if household_income > limit:
                    eligible = False
                    reasons.append(
                        f"입력 월소득이 {as_of.year}년 {request.household_size}인 가구 "
                        f"기준 중위소득 {ratio:.0%} 한도 {limit:,}원을 초과"
                    )
                else:
                    reasons.append(
                        f"입력 월소득 기준 {as_of.year}년 {request.household_size}인 가구 "
                        f"기준 중위소득 {ratio:.0%} 한도 {limit:,}원 이하"
                    )
                    reasons.append("실제 심사는 운영기관의 소득인정 기준 재확인 필요")
    if (
        "금융소득" in text
        or "금융소득" in str(row.get("plcyAplyMthdCn") or "")
        or "미래적금" in str(row.get("plcyNm") or "")
    ):
        if request.financial_income_taxed is None:
            if eligible:
                missing_fields.append("financial_income_taxed")
                reasons.append("금융소득종합과세 대상 여부 입력 필요")
        elif request.financial_income_taxed:
            eligible = False
            reasons.append("금융소득종합과세 이력으로 과세특례 적용 제한")
        else:
            reasons.append("금융소득종합과세 제한조건 충족")
    benefit_tier = "standard"
    if "우대형" in text:
        if request.is_sme_employee is None:
            if eligible:
                missing_fields.append("is_sme_employee")
                reasons.append("우대형 판정을 위한 중소기업 재직 여부 입력 필요")
        elif request.is_sme_employee:
            benefit_tier = "preferential_possible"
            reasons.append("중소기업 재직조건 충족, 우대형 세부 자격 확인 필요")
        else:
            reasons.append("중소기업 재직조건 미충족으로 일반형 적용")
    if "금리" in text and ("자율 결정" in text or "자율결정" in text):
        reasons.append("취급 금융기관 금리 추가 정보 필요(현재 예상액에는 은행이자 미포함)")
    if row.get("addAplyQlfcCndCn") or row.get("ptcpPrpTrgtCn"):
        reasons.append("추가 자격조건은 운영기관 확인 필요")

    policy_id = str(row.get("plcyNo") or "")
    if eligible:
        eligible = _apply_dynamic_gates(
            policy_id, gate_registry, request, eligible, missing_fields, reasons
        )

    missing_fields = list(dict.fromkeys(missing_fields))
    needs_verification = any("확인 필요" in reason or "대조 필요" in reason for reason in reasons)
    qualification_status = (
        "ineligible"
        if not eligible
        else "needs_input"
        if missing_fields
        else "needs_verification"
        if needs_verification
        else "confirmed"
    )
    # qualification_status는 "확인 필요" 계열 문구(운영기관 재확인, 우대형 세부
    # 자격 확인 등)가 하나라도 있으면 needs_verification으로 굳어지는데, 중위소득
    # 조건이 있는 정책은 그 문구가 항상 붙어 confirmed에 절대 도달하지 못한다.
    # 그래서 우대형 승격은 사용자가 입력한 is_sme_employee=True만으로 판단하고,
    # 운영기관 최종 확인 필요 여부는 qualification_status/reason에서 계속 안내한다.
    if benefit_tier == "preferential_possible" and eligible:
        benefit_tier = "preferential"

    return PolicyBenefit(
        policy_id=policy_id,
        name=str(row.get("plcyNm") or ""),
        eligible=eligible,
        monthly_limit=monthly,
        estimated_support=support,
        maturity_months=months,
        # 실제 신청 링크(aplyUrlAddr)가 있으면 그걸 우선한다 — 참고 URL(refUrlAddr*)은
        # 대개 소관기관 홈페이지일 뿐 신청 페이지가 아니다(예: kofpi.or.kr).
        source_url=str(
            row.get("aplyUrlAddr")
            or row.get("source_url")
            or row.get("refUrlAddr1")
            or row.get("refUrlAddr2")
            or ""
        ),
        effective_date=_date_value(row.get("lastMdfcnDt")) or str(as_of),
        reason="; ".join(reasons) or "명시된 기본조건 충족",
        reason_items=tuple(reasons) or ("명시된 기본조건 충족",),
        application_open=application_open,
        support_rate=_support_rate(text),
        preferential_support_rate=_preferential_support_rate(text),
        qualification_status=qualification_status,
        benefit_tier=benefit_tier,
        missing_qualification_fields=tuple(missing_fields),
        is_stipend_program=_is_stipend_program(text),
    )


def map_welfare_policy_row(
    row: Mapping[str, Any],
    request: RoadmapRequest,
    *,
    as_of: date,
    gate_registry: DynamicGateRegistry | None = None,
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
    missing_fields: list[str] = []
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
        # youth_policy와 동일한 방식(median_income_limit)으로 실제 소득 대비 비율을 계산한다.
        # 예전엔 "중위소득" 문구만 있으면 실제 비교 없이 무조건 eligible=False로 고정해,
        # 이 조건이 있는 welfare_service 레코드가 어떤 프로필로도 후보가 될 수 없었다.
        # 나이 조건으로 이미 탈락(eligible=False)한 상품이면 추가로 캐묻지 않는다.
        household_income = effective_household_monthly_income(request)
        if household_income is None:
            if eligible:
                missing_fields.append("household_monthly_income")
                reasons.append("가구 중위소득 판정을 위한 월소득 입력 필요")
        elif request.household_size is None:
            if eligible:
                missing_fields.append("household_size")
                reasons.append("가구 중위소득 판정을 위한 가구원 수 입력 필요")
        else:
            income_limit = median_income_limit(target, as_of.year, request.household_size)
            if income_limit is None:
                reasons.append("가구 중위소득 기준연도 또는 비율 확인 필요")
            else:
                ratio, limit = income_limit
                if household_income > limit:
                    eligible = False
                    reasons.append(
                        f"입력 월소득이 {as_of.year}년 {request.household_size}인 가구 "
                        f"기준 중위소득 {ratio:.0%} 한도 {limit:,}원을 초과"
                    )
                else:
                    reasons.append(
                        f"입력 월소득 기준 {as_of.year}년 {request.household_size}인 가구 "
                        f"기준 중위소득 {ratio:.0%} 한도 {limit:,}원 이하"
                    )
                    reasons.append("실제 심사는 운영기관의 소득인정 기준 재확인 필요")
    if any(term in target for term in ("북향민", "농업인", "어업인", "무주택")):
        # 이 대상군(북한이탈주민/농업인/어업인/무주택자) 여부를 물어볼 전용 프로필 필드가
        # 아직 없다 — 그렇다고 예전처럼 무조건 배제(eligible=False)하면 해당 신분에 실제로
        # 해당하는 사용자에게도 영원히 후보가 안 뜬다. 중위소득 조건과 같은 방식으로
        # "확인 필요" 상태로만 남기고 최종 판단은 운영기관에 위임한다(영구 배제하지 않음).
        reasons.append("대상자 특수조건(운영기관) 확인 필요")

    policy_id = str(row.get("servId") or "")
    if eligible:
        eligible = _apply_dynamic_gates(
            policy_id, gate_registry, request, eligible, missing_fields, reasons
        )

    missing_fields = list(dict.fromkeys(missing_fields))
    needs_verification = any("확인 필요" in reason for reason in reasons)
    qualification_status = (
        "ineligible"
        if not eligible
        else "needs_input"
        if missing_fields
        else "needs_verification"
        if needs_verification
        else "confirmed"
    )

    return PolicyBenefit(
        policy_id=policy_id,
        name=str(row.get("servNm") or ""),
        eligible=eligible,
        monthly_limit=monthly,
        estimated_support=support,
        maturity_months=months,
        source_url=str(row.get("source_url") or row.get("servDtlLink") or ""),
        effective_date=f"{row.get('crtrYr') or as_of.year}-01-01",
        reason="; ".join(reasons) or "명시된 기본조건 충족",
        reason_items=tuple(reasons) or ("명시된 기본조건 충족",),
        application_open=None,
        support_rate=_support_rate(benefit),
        preferential_support_rate=_preferential_support_rate(benefit),
        qualification_status=qualification_status,
        missing_qualification_fields=tuple(missing_fields),
        is_stipend_program=_is_stipend_program(benefit),
    )


class SqliteSavingsProductRepository:
    def __init__(self, connection_factory: ConnectionFactory):
        self.connection_factory = connection_factory

    def find_candidates(self, request: RoadmapRequest) -> list[SavingsProduct]:
        query = """
            SELECT b.dcls_month, b.fin_co_no, b.fin_prdt_cd, b.kor_co_nm,
                   b.fin_prdt_nm, b.etc_note, b.dcls_strt_day, b.source_url,
                   o.intr_rate_type, o.rsrv_type, o.save_trm, o.intr_rate, o.intr_rate2
              FROM finlife_saving_base b
              JOIN finlife_saving_option o
                USING (dcls_month, fin_co_no, fin_prdt_cd)
             WHERE o.intr_rate IS NOT NULL
               AND o.intr_rate_type = 'S'
               AND o.save_trm NOT GLOB '*[^0-9]*'
               AND CAST(o.save_trm AS INTEGER) <= ?
             ORDER BY CAST(o.save_trm AS INTEGER) DESC,
                      o.intr_rate2 IS NULL, o.intr_rate2 DESC,
                      o.intr_rate DESC, b.fin_prdt_nm
        """
        with self.connection_factory() as connection:
            rows = connection.execute(query, (request.horizon_months,)).fetchall()
            products = [map_savings_row(dict(row)) for row in rows]
        return [product for product in products if product is not None]


class SqlitePolicyRepository:
    def __init__(
        self,
        connection_factory: ConnectionFactory,
        *,
        as_of: date | None = None,
        rule_catalog: PolicyRuleCatalog | None = None,
        gate_registry: DynamicGateRegistry | None = None,
    ):
        self.connection_factory = connection_factory
        self.as_of = as_of or date.today()
        self.rule_catalog = rule_catalog
        self.gate_registry = gate_registry

    def find_candidates(self, request: RoadmapRequest) -> list[PolicyBenefit]:
        with self.connection_factory() as connection:
            youth_rows = connection.execute(
                'SELECT * FROM youth_policy WHERE "plcyAprvSttsCd" = ?',
                ("0044002",),
            ).fetchall()
            youth = [
                map_youth_policy_row(
                    dict(row), request, as_of=self.as_of, gate_registry=self.gate_registry
                )
                for row in youth_rows
            ]
            welfare_rows = connection.execute("SELECT * FROM welfare_service").fetchall()
            welfare = [
                map_welfare_policy_row(
                    dict(row), request, as_of=self.as_of, gate_registry=self.gate_registry
                )
                for row in welfare_rows
            ]
        candidates = [item for item in [*youth, *welfare] if item is not None]
        if self.rule_catalog is not None:
            candidates = [self.rule_catalog.apply(item, request) for item in candidates]
        unique: dict[str, PolicyBenefit] = {}
        for item in candidates:
            current = unique.get(item.name)
            if current is None or (item.eligible, item.estimated_support) > (
                current.eligible,
                current.estimated_support,
            ):
                unique[item.name] = item
        return sorted(unique.values(), key=lambda item: (not item.eligible, -item.estimated_support, item.name))


def sqlite_connection_factory(path: str | Path) -> ConnectionFactory:
    """공용 SQLite 파일용 연결 팩토리.

    기능 1은 같은 파일을 읽고 기능 2는 상품 조회와 대화 체크포인트 쓰기를 함께
    수행한다. WAL과 busy timeout을 모든 기능 2 연결에 적용해 짧은 동시 읽기/쓰기
    충돌을 기다렸다가 재시도하도록 한다.
    """
    db_path = Path(path).expanduser().resolve()
    db_path.parent.mkdir(parents=True, exist_ok=True)

    def connect() -> Any:
        connection = sqlite3.connect(str(db_path), timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA busy_timeout=30000")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    return connect


def sqlite_connection_factory_from_env() -> ConnectionFactory:
    path = os.getenv("SHARED_DB_PATH")
    if not path:
        raise RuntimeError("SHARED_DB_PATH 환경변수가 필요합니다.")
    return sqlite_connection_factory(path)
