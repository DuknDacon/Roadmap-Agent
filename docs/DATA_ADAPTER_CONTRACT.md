# 기능 2 데이터 교체 계약

## 목적

로컬 샘플 DB에서 성필님 운영 데이터로 전환할 때 에이전트 코드를 바꾸지 않는 것을 목표로 한다.

## 계층

```text
원천 API 응답
→ raw 스키마: ROADMAP_AGENT_DATA.md 필드와 원문 JSON 보존
→ Repository 어댑터: 단위·코드·날짜·자격조건 정규화
→ SavingsProduct / PolicyBenefit
→ 에이전트 계산
```

## 고정할 것

- finlife 결합 키: `dcls_month + fin_co_no + fin_prdt_cd`
- 온통청년 식별자: `plcyNo`
- 복지서비스 식별자: `servId`
- 복지서비스 목록 전체는 저장하지 않고 대상 선정·변경 확인에만 조회한다.
- 선정된 상세 레코드에는 목록의 `servNm`, `servDtlLink`를 병합한다.
- 승인 정책 조건: `plcyAprvSttsCd=0044002`
- 원문 설명과 `raw_data`를 요약하거나 제거하지 않는다.
- 공백·누락값을 임의의 `0`으로 바꾸지 않는다.
- 출처, 기준일·공시일, 수집일, 마지막 확인일을 보존한다.

## 교체되는 것

- 로컬 단계: JSON fixture를 `raw` 스키마에 UPSERT하는 저장소
- 통합 단계: 성필님 조회 API 또는 DB 응답을 읽는 저장소

두 구현은 모두 `SavingsProductRepository`와 `PolicyRepository`를 만족해야 한다. 에이전트는 원천 테이블명이나 API URL을 직접 알지 않는다.

## 단위 및 코드 처리

- `earnMinAmt`, `earnMaxAmt`의 단위는 원천 명세 확인 전 원문 문자열로 유지한다.
- `max_limit=null`이면 `etc_note`에서 한도를 확인하되 확인되지 않으면 무제한으로 간주하지 않는다.
- 코드값과 설명문 조건이 충돌하면 자동 자격 확정 대신 `추가 확인 필요`로 반환한다.
- 신청기간이 종료됐거나 기준일이 오래된 정책은 가입 가능 후보에서 제외하고 설명용 근거로만 사용할 수 있다.
