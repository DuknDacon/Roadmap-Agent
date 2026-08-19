# syntax=docker/dockerfile:1
#
# 기능② 로드맵 백엔드 이미지 (Roadmap-Agent 라이브러리 + FastAPI 서버, 한 이미지).
# 레지스트리는 쓰지 않는다 — 빌드는 로컬에서 끝내고, 완성된 이 이미지 하나만
# 파일(tar)로 서버에 직접 전달한다. 서버는 이 레포를 clone할 필요가 없다.
#
# 빌드 (이 레포 루트에서):
#   docker build -t seedup-roadmap-backend:<tag> .
#
# 파일로 내보내 서버에 전달:
#   docker save -o seedup-roadmap-backend-<tag>.tar seedup-roadmap-backend:<tag>
#   scp seedup-roadmap-backend-<tag>.tar <서버>:/path/to/
#   (서버에서) docker load -i seedup-roadmap-backend-<tag>.tar
FROM python:3.11-slim

WORKDIR /app

# Roadmap-Agent 라이브러리. 런타임에 필요한 것만 복사한다
# (.venv/tests/scripts/data/raw 등은 제외). data/rag 는 로컬 RAG 검색
# (LocalRagRetriever)이 참조하는 원문 폴더라 반드시 필요하다.
COPY pyproject.toml README.md ./
COPY src ./src
COPY data/rag ./data/rag
RUN pip install --no-cache-dir -e .

# FastAPI 어댑터. .env 파일은 절대 복사하지 않는다 — 컨테이너 실행 시점에 주입한다.
COPY backend/requirements.txt ./backend/requirements.txt
COPY backend/app ./backend/app
RUN pip install --no-cache-dir -r backend/requirements.txt

ENV PYTHONUNBUFFERED=1
EXPOSE 8001

CMD ["uvicorn", "backend.app.main:app", "--host", "0.0.0.0", "--port", "8001"]
