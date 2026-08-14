# 기능 2 프로토타입 UI

현재 Roadmap Agent의 입력·계산·RAG·설명 흐름을 검증하기 위한 Streamlit 1차 초안입니다.
최종 서비스 UI와 디자인 시스템은 별도 폴더에서 구축하며, 이 폴더는 기능 검증 후 제거할 수
있습니다.

## 실행

저장소 상위 가상환경에 UI 의존성을 설치합니다.

```bash
../.venv/bin/python -m pip install -e '.[ui]'
```

Roadmap-Agent 디렉터리에서 실행합니다.

```bash
../.venv/bin/python -m streamlit run prototype_ui/app.py
```

브라우저에서 기본 기능을 확인할 때는 Gemini 토글을 끄고 사용할 수 있습니다. 벡터 RAG와
Gemini 설명을 켜면 `.env`의 설정을 서버 프로세스에서 사용하며 키는 브라우저로 전달하지
않습니다.

시도·시군구 선택 목록은 행정안전부 행정표준코드관리시스템의 현존 법정동 코드를 사용합니다.
행정구역 개편 후 목록을 갱신하려면 다음 명령을 실행합니다.

```bash
../.venv/bin/python scripts/fetch_region_codes.py
```
