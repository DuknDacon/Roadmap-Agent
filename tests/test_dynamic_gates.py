import json
import tempfile
import unittest
from pathlib import Path

from roadmap_agent.dynamic_gates import DynamicGate, DynamicGateRegistry


def _write_gate_file(directory: Path, policy_id: str, *, status: str, gates: list[dict]) -> None:
    (directory / f"{policy_id}.json").write_text(
        json.dumps(
            {
                "policy_id": policy_id,
                "policy_name": "테스트 상품",
                "content_hash": "sha256:test",
                "status": status,
                "gates": gates,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


class DynamicGateRegistryTest(unittest.TestCase):
    def test_missing_directory_returns_empty_registry(self):
        registry = DynamicGateRegistry.from_directory(Path("/does/not/exist"))
        self.assertEqual(registry.gates_for("P1"), [])

    def test_loads_only_verified_gates(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            _write_gate_file(
                directory,
                "P1",
                status="verified",
                gates=[
                    {
                        "gate_id": "artist_certification",
                        "question": "예술활동증명을 받으셨나요?",
                        "hint": "문체부 인증입니다.",
                    }
                ],
            )
            _write_gate_file(
                directory,
                "P2",
                status="extracted",
                gates=[{"gate_id": "unreviewed", "question": "검수 안 된 질문"}],
            )
            registry = DynamicGateRegistry.from_directory(directory)

            gates = registry.gates_for("P1")
            self.assertEqual(len(gates), 1)
            self.assertEqual(
                gates[0],
                DynamicGate(
                    policy_id="P1",
                    gate_id="artist_certification",
                    question="예술활동증명을 받으셨나요?",
                    hint="문체부 인증입니다.",
                    policy_name="테스트 상품",
                ),
            )
            # status가 extracted(미검수)인 파일은 로드되지 않는다 — 검증 안 된
            # LLM 추출 결과가 실제 자격판정에 새는 걸 로드 시점에 막는다.
            self.assertEqual(registry.gates_for("P2"), [])

    def test_composite_id(self):
        self.assertEqual(
            DynamicGateRegistry.composite_id("P1", "artist_certification"),
            "P1:artist_certification",
        )

    def test_find_gates_mentioning_matches_by_stripped_noun(self):
        registry = DynamicGateRegistry({
            "P1": [
                DynamicGate(
                    policy_id="P1",
                    gate_id="no_recent_company_employment",
                    question="참여기업에서 근무한 이력이 없으신가요?",
                    hint="신청일 기준 6개월 내 해당 참여기업 근무 이력이 있는 자는 지원할 수 없습니다.",
                    policy_name="사회연대경제 청년일경험",
                )
            ]
        })
        matches = registry.find_gates_mentioning("참여기업이 뭐야?")
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0].gate_id, "no_recent_company_employment")

    def test_find_gates_mentioning_returns_empty_when_no_term_matches(self):
        registry = DynamicGateRegistry({
            "P1": [
                DynamicGate(
                    policy_id="P1", gate_id="g1",
                    question="참여기업에서 근무한 이력이 없으신가요?",
                    hint="참여기업 근무 이력이 있으면 안 됩니다.",
                )
            ]
        })
        self.assertEqual(registry.find_gates_mentioning("연금저축 세액공제가 뭐야?"), [])


if __name__ == "__main__":
    unittest.main()
