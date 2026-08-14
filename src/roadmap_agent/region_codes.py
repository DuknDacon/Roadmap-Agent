from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class RegionOption:
    code: str
    name: str


@dataclass(frozen=True)
class ProvinceOption(RegionOption):
    districts: tuple[RegionOption, ...]


def load_region_codes(path: Path) -> tuple[ProvinceOption, ...]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return tuple(
        ProvinceOption(
            code=str(item["code"]),
            name=str(item["name"]),
            districts=tuple(
                RegionOption(str(district["code"]), str(district["name"]))
                for district in item.get("districts", [])
            ),
        )
        for item in payload["regions"]
    )


def filter_regions(options: tuple[RegionOption, ...], query: str) -> list[RegionOption]:
    keyword = "".join(query.split()).casefold()
    if not keyword:
        return list(options)
    return [option for option in options if keyword in option.name.replace(" ", "").casefold()]
