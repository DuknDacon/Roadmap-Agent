FROM pgvector/pgvector:pg16

# 로컬 통합 테스트/데모 전달용 시드 이미지. 최초 컨테이너 기동(빈 볼륨) 시
# 아래 두 스크립트가 순서대로 실행되어 스키마 생성 + 샘플(fixture) 데이터
# 적재까지 자동으로 끝난다. 실제 서비스 DB가 아니며, 성필님이 준비하는
# 실제 서버 DB가 확정되면 이 이미지 대신 그쪽에 연결한다.
COPY db/init/001_schema.sql /docker-entrypoint-initdb.d/01_schema.sql
COPY db/seed/sample_data.sql /docker-entrypoint-initdb.d/02_sample_data.sql
