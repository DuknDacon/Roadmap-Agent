-- 기존 로컬 DB를 "목록 전체 저장 없이 선정 상세만 저장" 구조로 전환한다.
-- 기존 목록 테이블은 데이터 손실 방지를 위해 삭제하지 않는다.

ALTER TABLE raw.welfare_service_detail
    ADD COLUMN IF NOT EXISTS "servDtlLink" text;

ALTER TABLE raw.welfare_service_detail
    DROP CONSTRAINT IF EXISTS "welfare_service_detail_servId_fkey";
