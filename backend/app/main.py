from __future__ import annotations

import os
import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .schemas import RoadmapCreateRequest, RoadmapResponse
from .service import create_roadmap


app = FastAPI(title="SeedUp Roadmap API", version="0.1.0")
logger = logging.getLogger(__name__)
origins = [value.strip() for value in os.getenv("CORS_ORIGINS", "http://localhost:3000").split(",")]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_origin_regex=r"^https?://(?:localhost|127\.0\.0\.1)(?::\d+)?$",
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type"],
)


@app.exception_handler(RequestValidationError)
async def validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
    detail = [
        {"field": ".".join(str(item) for item in error["loc"]), "message": error["msg"], "type": error["type"]}
        for error in exc.errors()
    ]
    return JSONResponse(
        status_code=422,
        content={"code": "REQUEST_INVALID", "message": "입력값을 다시 확인해 주세요.", "detail": detail},
    )


@app.exception_handler(ValueError)
async def domain_error(_: Request, exc: ValueError) -> JSONResponse:
    return JSONResponse(
        status_code=400,
        content={"code": "ROADMAP_INVALID", "message": str(exc), "detail": None},
    )


@app.exception_handler(Exception)
async def unexpected_error(_: Request, exc: Exception) -> JSONResponse:
    logger.exception("Unhandled roadmap API error", exc_info=exc)
    return JSONResponse(
        status_code=500,
        content={"code": "ROADMAP_FAILED", "message": "로드맵 계산 중 문제가 발생했습니다.", "detail": None},
    )


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/v1/roadmaps", response_model=RoadmapResponse, response_model_by_alias=True)
def roadmap(payload: RoadmapCreateRequest) -> RoadmapResponse:
    return create_roadmap(payload)
