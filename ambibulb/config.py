"""Bulb configuration: load and validate the user's bulbs file."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


class ConfigError(Exception):
    """Raised when a bulbs config file is missing or malformed."""


@dataclass(frozen=True)
class BulbConfig:
    """One bulb's local-control credentials and address."""

    name: str
    id: str
    key: str
    ip: str = "Auto"
    version: float = 3.3

    @classmethod
    def from_dict(cls, raw: dict) -> "BulbConfig":
        for field in ("id", "key"):
            if not raw.get(field):
                raise ConfigError(f"bulb entry missing '{field}': {raw!r}")
        return cls(
            name=str(raw.get("name") or raw["id"][:8]),
            id=str(raw["id"]),
            key=str(raw["key"]),
            ip=str(raw.get("ip", "Auto")),
            version=float(raw.get("version", 3.3)),
        )


def load_bulbs(path: Path | str) -> list[BulbConfig]:
    """Read and validate a bulbs JSON file.

    Expected shape: {"bulbs": [{"name","id","key","ip","version"}, ...]}
    Raises ConfigError with an actionable message on any problem.
    """
    path = Path(path)
    if not path.exists():
        raise ConfigError(
            f"Config not found: {path}\n"
            "Create it from bulbs.example.json, or run `ambibulb discover` "
            "to find your bulbs first."
        )
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        raise ConfigError(f"{path} is not valid JSON: {exc}") from exc

    entries = data.get("bulbs") if isinstance(data, dict) else None
    if not entries:
        raise ConfigError(f"No bulbs listed in {path} (expected a 'bulbs' array).")

    bulbs = [BulbConfig.from_dict(e) for e in entries]
    ids = [b.id for b in bulbs]
    if len(set(ids)) != len(ids):
        raise ConfigError(f"Duplicate bulb id in {path}.")
    return bulbs
