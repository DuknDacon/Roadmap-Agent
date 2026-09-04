"""API 응답 스키마와 service.py 매핑이 어긋나지 않는지 지키는 테스트.

conversation 계층에만 필드를 추가하고 backend/app/schemas.py 에 넣는 걸
빠뜨려도 라이브러리 테스트는 전부 통과한다 — 실제로 suggestedReplies 를
추가할 때 스키마 편집이 누락된 채 통과했고, 배포 직전에야 발견했다.
그 종류의 누락을 여기서 잡는다.
"""

from backend.app.schemas import RoadmapResponse


def test_conversation_level_fields_are_exposed_by_the_api():
    """대화 단위로 붙는 필드(카드/제안/근거)가 응답 스키마에 있어야 한다."""
    fields = RoadmapResponse.model_fields
    for name, alias in (
        ("policy_eligibility_cards", "policyEligibilityCards"),
        ("suggested_replies", "suggestedReplies"),
        ("sources", None),
    ):
        assert name in fields, f"{name} 이 RoadmapResponse 에 없다"
        if alias is not None:
            assert fields[name].alias == alias, f"{name} 의 alias 가 {alias} 가 아니다"
        # 대화가 아닌 turn(로드맵 최초 생성 등)에서는 비어 있어야 하므로
        # 기본값이 없으면 그 turn의 응답 생성이 통째로 실패한다.
        assert fields[name].default_factory is not None, f"{name} 에 기본값이 없다"


def test_service_passes_every_conversation_field_to_the_response():
    """service.py 가 위 필드를 실제로 채워 넘기는지 확인한다.

    스키마에 필드만 있고 service.py 가 안 넘기면 항상 빈 값이 나가므로,
    "필드는 있는데 화면엔 안 보이는" 상태가 된다(sources 가 그랬다).
    """
    from pathlib import Path

    source = Path(__file__).resolve().parents[1] / "backend" / "app" / "service.py"
    text = source.read_text(encoding="utf-8")
    for keyword in ("policyEligibilityCards=", "suggestedReplies=", "sources="):
        assert keyword in text, f"service.py 가 {keyword} 를 응답에 넘기지 않는다"
