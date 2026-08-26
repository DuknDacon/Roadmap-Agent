# SeedUp Roadmap Agent

SeedUp의 기능 2인 **AI 자산 관리 및 시드머니 로드맵** 전용 저장소입니다.

기존 TradingAgents 코드는 멀티에이전트의 개념적 구조만 참고하고 제거했습니다. 종목 분석·매매 결정·시장 뉴스 수집 코드는 사회초년생의 적금·투자·정책상품 로드맵과 책임 범위가 달라 재사용하지 않습니다.

## 에이전트 뼈대

1. **적금 에이전트**: 실제 적금상품 비교와 세후 만기액 계산
2. **투자 에이전트**: 위험성향별 자산군 배분 및 하락·기준·상승 설명
3. **정책상품 에이전트**: 자격판정과 공식 지원혜택 계산
4. **균형 에이전트**: 정책상품·적금·투자 자산군 조합
5. **최종 결정권자 에이전트**: 계산된 점수와 순위를 유지하며 추천 이유 설명

각 에이전트 아래에 입력 데이터, 결정론적 계산 도구, RAG 근거, 경고문이 배치됩니다. ISA·연금 설명과 운영용 RAG 검색은 별도 사용자 기능이 아니라 투자 에이전트 내부 도구입니다.

정책상품과 실제 적금상품은 성필님이 제공할 구조화 데이터 인터페이스를 연결하기 전까지 `data_status`에 미연결 상태를 명시합니다. 숫자를 LLM이 임의 생성하지 않습니다.

## 구조

```text
src/roadmap_agent/
  intake.py               단계형 입력 정규화
  domain.py               요청·결과 모델
  calculators.py          결정론적 계산 함수
  agents.py               품목별 시나리오 노드
  repositories.py         공용 SQLite 적금·정책 Repository, 실제 API 필드 매핑
  policy_qualification.py 기준 중위소득 계산
  policy_rules.py         검증된 JSON 규칙 기반 자격판정 엔진(현재 프로덕션 미연결)
  retrieval.py            FAISS·BM25 하이브리드 RAG와 로컬 폴백 검색
  rag_chunking.py         헤딩·법령 조항 경계 보존 청커
  conversation.py         Agentic 대화 의도 분류·도구 실행
  conversation_graph.py   LangGraph 대화 스레드 상태
  gemini.py               Gemini 설명·대화 계획기·임베딩·웹 검색
  orchestrator.py         전체 흐름 및 LangGraph 구성
  cli.py                  로컬 실행 진입점
data/rag/                 ISA·연금·정책 공식 RAG 원문
data/source_docs/         수집 원문·API 가이드
scripts/                  RAG 인덱싱, 복지서비스·금융꿀팁 수집기
tests/                    단위 테스트
backend/                  SeedUp 프론트엔드가 호출하는 FastAPI 어댑터 (별도 README 참고)
```

## 로컬 실행

저장소 상위(`Roadmap-Agent/`와 `SeedUp/`의 공통 상위 폴더)에 준비된 **공용 가상환경**을 사용합니다. `backend/`(FastAPI 어댑터)도 같은 가상환경을 그대로 씁니다 — 따로 만들지 않습니다. 처음 준비하는 경우:

```bash
python -m venv ../.venv
../.venv/bin/pip install -r requirements.txt
../.venv/bin/pip install -e .
```

```bash
../.venv/bin/python -m unittest discover -s tests -v
PYTHONPATH=src ../.venv/bin/python -m roadmap_agent.cli \
  --monthly-budget 800000 --months 36 --goal 30000000 --risk balanced
```

공용 SQLite에 적재한 실제 상품·정책 데이터를 사용하는 종단 실행은 다음과 같습니다.

```bash
../.venv/bin/python -m roadmap_agent.cli \
  --sqlite \
  --monthly-budget 800000 \
  --months 36 \
  --goal 30000000 \
  --risk balanced \
  --age 28 \
  --annual-income 40000000 \
  --region-code 11:11110
```

`--sqlite`를 지정하면 `.env`의 `SHARED_DB_PATH`를 읽습니다. 적금은 실제 공시
금리·납입한도·기간으로 세후 만기액을
계산하고, 정책은 명시적으로 판정 가능한 자격과 지원액만 후보로 사용합니다.

## Gemini RAG 및 설명 생성

공식 Markdown은 제목 경계를 우선해 최대 1,800자, 중첩 없이 나눕니다. 기능 1과
같이 Gemini dense 임베딩(FAISS)과 Kiwi-BM25 결과를 RRF로 결합하며, parent 문맥을
최종 근거로 반환합니다.

```bash
../.venv/bin/python scripts/index_rag_documents.py
```

명령을 실행하면 `data/rag_index/`의 인덱스를 다시 생성합니다. 생성 후 하이브리드
검색과 Gemini 설명을 함께 확인합니다.

```bash
../.venv/bin/python -m roadmap_agent.cli \
  --sqlite --rag --gemini \
  --monthly-budget 800000 --months 36 --goal 30000000 --risk balanced \
  --age 28 --annual-income 40000000 --region-code 11:11110
```

Gemini는 계산 결과를 수정하지 않고 `explanation`만 생성합니다. 임베딩 API 또는 FAISS
검색에 장애가 생기면 로컬 키워드 검색으로, 설명 생성에 실패하면 기존 JSON 결과로
폴백합니다.

## 테스트용 샘플 데이터 수집

인증키는 `Roadmap-Agent/.env`에 직접 입력합니다. 이 파일은 Git에서 제외되며 에이전트가 읽지 않도록 저장소 지침에도 명시되어 있습니다. 수집기는 실행 시 `.env`를 자동으로 불러옵니다.

```bash
../.venv/bin/python scripts/fetch_feature2_samples.py \
  --source finlife \
  --source youth
```

복지서비스는 최신 전체 목록 API를 먼저 호출하고 자산형성 관련 후보 5개를 자동 선별한 뒤 상세 API를 호출합니다.

```bash
../.venv/bin/python scripts/fetch_feature2_samples.py \
  --source welfare
```

특정 서비스만 확인할 때는 `--welfare-service-id`를 여러 번 지정할 수 있습니다.

환경변수를 사용하지 않으면 `--source`로 지정한 인증키를 터미널에서 숨김 입력할 수 있습니다.
결과는 `data/fixtures/` 아래에 생성되며 인증키는 저장되지 않습니다.

금융꿀팁 API 키를 `.env`의 `FSS_OPEN_API_KEY`에 입력했다면 다음 명령으로 원본 JSON과 관련 RAG 문서를 생성합니다.

```bash
../.venv/bin/python scripts/fetch_fss_finance_tips.py
```

## 공용 SQLite

`.env`의 `SHARED_DB_PATH`에 기능 1·2가 함께 참조할 SQLite 파일 경로를 지정합니다.
초기 스키마 계약은 `db/sqlite_schema.sql`입니다.

```bash
mkdir -p data/shared
sqlite3 data/shared/seedup.sqlite < db/sqlite_schema.sql
```

SQLite에는 PostgreSQL의 `raw` schema namespace가 없으므로 테이블은
`finlife_saving_base`, `finlife_saving_option`, `youth_policy`,
`welfare_service_detail`처럼 최상위에 둡니다. 성필님 제공 DB도 이 컬럼 계약을
따르면 별도 코드 변경 없이 교체할 수 있습니다. 기능 2의 LangGraph 체크포인트와
세션 테이블도 같은 파일에 생성되며 WAL과 30초 busy timeout을 사용합니다.

수집한 세 fixture를 성필님 전달 명세와 같은 `raw` 컬럼에 UPSERT합니다.

```bash
../.venv/bin/python scripts/load_feature2_samples.py
```

명령 끝에 finlife 상품·옵션, 온통청년 정책, 복지서비스 상세의 적재 건수가 출력됩니다. 같은 명령을 다시 실행해도 기본 키 기준으로 갱신되며 중복 행을 만들지 않습니다.

## 백엔드·프론트엔드 실행 (기능 2 전체 스택)

기능②의 FastAPI 백엔드(`backend/`)는 이 저장소 안에 있습니다. SeedUp 저장소에는 프론트엔드(Next.js)만 있고, `NEXT_PUBLIC_ROADMAP_API_URL`로 이 백엔드를 HTTP로 호출합니다. 브라우저에서 기능 2를 끝까지 확인하려면 두 프로세스를 함께 띄웁니다.

**1. 백엔드 (FastAPI, 이 저장소)** — 위 "로컬 실행" 절의 같은 가상환경을 그대로 사용합니다. 자세한 설정은 [`backend/README.md`](backend/README.md) 참고.

```bash
source ../.venv/bin/activate
pip install -r backend/requirements.txt   # roadmap_agent 자체는 이미 위에서 editable 설치됨
uvicorn backend.app.main:app --reload --port 8001
```

- 헬스체크: `http://localhost:8001/health`
- API 문서: `http://localhost:8001/docs`
- 시작 시 저장소 루트의 `.env`를 먼저 읽고 `backend/.env`로 보완합니다. 브라우저에는 이 값이 전달되지 않습니다.
- `SHARED_DB_PATH`를 설정해야 실제 적금·정책 Repository가 활성화되고,
  `ENABLE_RAG=true`이면 FAISS+BM25 인덱스를 사용합니다. `ENABLE_GEMINI=true` +
  `GEMINI_API_KEY`가 있어야 최종 설명과 대화 계획기가 동작합니다.

**2. SeedUp 프론트엔드 (Next.js)** — 별도 터미널에서 `SeedUp/`:

```bash
cd ../SeedUp
npm install
NEXT_PUBLIC_ROADMAP_API_URL=http://localhost:8001 npm run dev
```

- 기본 접속: `http://localhost:3000`
- `NEXT_PUBLIC_ROADMAP_API_URL`을 생략하면 `http://localhost:8001`을 기본값으로 사용합니다.

두 프로세스가 모두 떠 있으면 `http://localhost:3000`에서 최초 로드맵 생성부터 조건 변경·후속 질문까지 실제 브라우저로 확인할 수 있습니다. 자동화된 브라우저(e2e) 테스트는 아직 없으므로, 현재는 이 수동 실행 경로가 유일한 종단 확인 방법입니다.

## 배포 (Docker)

이 저장소 루트의 `Dockerfile`이 `roadmap_agent`와 `backend/`를 하나의 이미지로 빌드합니다.
기능 2 전용 RAG 인덱스(`data/rag_index/index.faiss`, `documents.pkl`)도 이 이미지에
포함됩니다. 공용 볼륨으로 분리하는 것은 기능 1·2가 함께 참조하는 `seedup.sqlite`뿐입니다.
RAG 인덱스를 다시 생성한 경우 Docker 이미지를 다시 빌드해야 변경 내용이 반영됩니다.
레지스트리는 쓰지 않고, 빌드는 로컬에서 끝낸 뒤 완성된 이미지 파일 하나만 서버로 전달합니다.

```bash
docker build -t seedup-roadmap-backend:v1 .
docker save -o seedup-roadmap-backend-v1.tar seedup-roadmap-backend:v1
scp seedup-roadmap-backend-v1.tar <서버>:/path/to/
# 서버에서:
docker load -i /path/to/seedup-roadmap-backend-v1.tar
docker run -d --env-file backend/.env \
  -v /path/to/shared-data:/app/data/shared \
  -p 8001:8001 seedup-roadmap-backend:v1
```

Compose 실행에서도 `data/rag_index`를 별도 마운트하지 않습니다. 이미지 내부의
`/app/data/rag_index`를 사용하고, `${SHARED_DB_HOST_DIR:-./data/shared}`만
`/app/data/shared`에 연결합니다.

서버는 이 레포도 SeedUp 레포도 clone할 필요가 없습니다 (프론트엔드를 같은 서버에서 돌릴 경우 그쪽만 별도로 SeedUp을 clone). 자세한 배경과 현재 진행 상태는 Notion 「[기능 2] 서버 배포 준비」 페이지를 참고합니다.

## 설계 원칙

1. RAG는 설명과 출처 제시에만 사용합니다.
2. 금리·한도·자격판정은 버전이 있는 구조화 데이터와 함수가 담당합니다.
3. LLM은 계산값을 변경하지 않고 설명·질문 분류·문장 생성을 담당합니다.
4. 특정 종목이나 펀드의 매수를 추천하지 않고 자산군 수준의 범위만 제시합니다.
5. 모든 결과는 참고용이며 실제 가입 전 최신 약관과 공식 원문을 확인합니다.

## 기능 검증용 UI 초안

최종 UI와 분리된 Streamlit 초안은 `prototype_ui/`에 있습니다. 기능형 필수 입력으로 최초
로드맵을 만든 뒤 대화로 월 투입액·목표금액·투자비중 등을 변경해 재계산하는 흐름을
검증합니다. 설치·실행 방법은 `prototype_ui/README.md`를 따릅니다.
