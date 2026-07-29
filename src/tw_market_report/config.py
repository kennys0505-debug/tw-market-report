from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class ReportConfig:
    raw: dict[str, Any]
    root: Path

    @property
    def sources(self) -> dict[str, str]:
        return self.raw["sources"]

    @property
    def module_weights(self) -> dict[str, float]:
        return {key: float(value) for key, value in self.raw["module_weights"].items()}

    @property
    def shadow_mode(self) -> bool:
        return bool(self.raw.get("shadow_mode", True))

    @property
    def exposure_ranges(self) -> dict[str, list[int]]:
        return self.raw["exposure_ranges"]


def load_config(path: str | Path | None = None) -> ReportConfig:
    config_path = Path(path) if path else Path("config/report.json")
    config_path = config_path.resolve()
    with config_path.open("r", encoding="utf-8") as handle:
        raw = json.load(handle)
    return ReportConfig(raw=raw, root=config_path.parent.parent)

