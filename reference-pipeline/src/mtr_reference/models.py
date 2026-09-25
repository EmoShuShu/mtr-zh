from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class OfficialUnit:
    kind: str
    text: str
    pages: tuple[int, ...]

    def as_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "text": self.text, "pages": list(self.pages)}


@dataclass
class OfficialSection:
    key: str
    en: str
    units: list[OfficialUnit] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "en": self.en,
            "units": [unit.as_dict() for unit in self.units],
        }


@dataclass
class OfficialDocument:
    source_sha256: str
    sections: list[OfficialSection]

    def as_dict(self) -> dict[str, Any]:
        return {
            "formatVersion": 1,
            "officialPdfSha256": self.source_sha256,
            "sectionCount": len(self.sections),
            "unitCount": sum(len(section.units) for section in self.sections),
            "sections": [section.as_dict() for section in self.sections],
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "OfficialDocument":
        sections = [
            OfficialSection(
                section["key"],
                section["en"],
                [
                    OfficialUnit(unit["kind"], unit["text"], tuple(unit["pages"]))
                    for unit in section["units"]
                ],
            )
            for section in value["sections"]
        ]
        return cls(value["officialPdfSha256"], sections)
