"""tracer.py – transparenter Rechenweg: jeder Schritt wird mit Formel, Werten und Rechtsbezug protokolliert."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Schritt:
    gruppe: str
    titel: str
    formel: str
    wert: float | str | None
    einheit: str = ""
    hinweis: str = ""


@dataclass
class Tracer:
    schritte: list[Schritt] = field(default_factory=list)

    def add(self, gruppe: str, titel: str, formel: str, wert, einheit: str = "", hinweis: str = ""):
        self.schritte.append(Schritt(gruppe, titel, formel, wert, einheit, hinweis))
        return wert

    def gruppen(self) -> list[str]:
        seen: list[str] = []
        for s in self.schritte:
            if s.gruppe not in seen:
                seen.append(s.gruppe)
        return seen

    def fuer(self, gruppe: str) -> list[Schritt]:
        return [s for s in self.schritte if s.gruppe == gruppe]
