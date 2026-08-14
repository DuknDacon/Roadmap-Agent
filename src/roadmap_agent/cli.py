from __future__ import annotations

import argparse
import json
from pathlib import Path

from .config import load_env_file
from .domain import RiskProfile, RoadmapRequest
from .gemini import GeminiEmbeddingClient, GeminiRoadmapExplainer
from .orchestrator import run_roadmap
from .repositories import (
    PostgresPolicyRepository,
    PostgresSavingsProductRepository,
    postgres_connection_factory_from_env,
)
from .retrieval import FallbackRagRetriever, LocalRagRetriever, PostgresVectorRagRetriever


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="SeedUp Roadmap Agent 초안 실행")
    parser.add_argument("--monthly-budget", type=int, required=True)
    parser.add_argument("--months", type=int, required=True)
    parser.add_argument("--goal", type=int)
    parser.add_argument("--risk", choices=[item.value for item in RiskProfile], required=True)
    parser.add_argument("--age", type=int)
    parser.add_argument("--annual-income", type=int)
    parser.add_argument("--has-emergency-fund", action="store_true")
    parser.add_argument("--question", default="")
    parser.add_argument("--region-code", help="시도:시군구 코드, 예: 11:11110")
    parser.add_argument("--postgres", action="store_true", help="로컬 PostgreSQL 실데이터 사용")
    parser.add_argument("--vector-rag", action="store_true", help="Gemini 임베딩과 pgvector 검색 사용")
    parser.add_argument("--gemini", action="store_true", help="Gemini로 최종 사용자 설명 생성")
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    return parser


def main() -> int:
    args = build_parser().parse_args()
    request = RoadmapRequest(
        monthly_budget=args.monthly_budget,
        horizon_months=args.months,
        target_amount=args.goal,
        risk_profile=RiskProfile(args.risk),
        age=args.age,
        annual_income=args.annual_income,
        region_code=args.region_code,
        has_emergency_fund=args.has_emergency_fund,
        question=args.question,
    )
    savings_repository = None
    policy_repository = None
    retriever = None
    explainer = None
    if args.postgres or args.vector_rag or args.gemini:
        load_env_file(args.env_file)
    if args.postgres:
        factory = postgres_connection_factory_from_env()
        savings_repository = PostgresSavingsProductRepository(factory)
        policy_repository = PostgresPolicyRepository(factory)
    if args.vector_rag:
        if not args.postgres:
            raise SystemExit("--vector-rag는 --postgres와 함께 사용해야 합니다.")
        local = LocalRagRetriever(Path(__file__).resolve().parents[2] / "data" / "rag")
        retriever = FallbackRagRetriever(
            PostgresVectorRagRetriever(factory, GeminiEmbeddingClient()),
            local,
        )
    if args.gemini:
        explainer = GeminiRoadmapExplainer()
    print(
        json.dumps(
            run_roadmap(
                request,
                policy_repository=policy_repository,
                savings_repository=savings_repository,
                retriever=retriever,
                explainer=explainer,
            ).to_dict(),
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
