from __future__ import annotations

import os
from pathlib import Path


def load_env_file(path: Path) -> None:
    """외부 패키지 없이 로컬 실행 환경을 로드한다. 기존 환경변수는 유지한다."""
    if not path.is_file():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        name = name.strip()
        value = value.strip().strip('"').strip("'")
        if name and name not in os.environ:
            os.environ[name] = value
