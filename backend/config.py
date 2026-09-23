"""Explicit local storage configuration shared by API and worker."""
from dataclasses import dataclass
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class Settings:
    state: Path
    data: Path
    outputs: Path
    bootstrap: bool = True
    lease_seconds: int = 120
    file_limit: int = 50 * 1024 * 1024
    body_limit: int = 151 * 1024 * 1024

    @classmethod
    def from_env(cls):
        return cls(
            Path(os.getenv("MONEY_GRAPH_STATE", str(ROOT / "runtime"))).resolve(),
            Path(os.getenv("MONEY_GRAPH_DATA", str(ROOT / "data"))).resolve(),
            Path(os.getenv("MONEY_GRAPH_OUTPUTS", str(ROOT / "outputs"))).resolve(),
            os.getenv("MONEY_GRAPH_BOOTSTRAP", "1") == "1",
        )

    @property
    def results(self):
        return self.state / "results"
