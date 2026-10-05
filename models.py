"""models.py – Eingabe-Datenmodelle (Dataclasses) inkl. JSON-Serialisierung."""
from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict, fields
from datetime import date

import config


def _filter(cls, d: dict) -> dict:
    names = {f.name for f in fields(cls)}
    return {k: v for k, v in d.items() if k in names}


@dataclass
class Person:
    geburtsdatum: str = "1967-05-10"          # ISO-Datum
    ep_aktuell: float = 34.0                  # Entgeltpunkte laut Rentenauskunft
    ep_pro_jahr: float = 1.3                  # erwartete EP je weiterem Erwerbsjahr
    wartezeit_jahre_35: float = 37.0          # anrechenbare Jahre für 35-Jahre-Wartezeit heute
    wartezeit_jahre_45: float = 40.0          # anrechenbare Jahre für 45-Jahre-Wartezeit heute
    brutto_jahr: float = 62_000.0             # aktuelles Jahresbrutto (Arbeitnehmer)
    steuerklasse: str = "1"                   # 1, 3, 4, 4F, 5
    partner_einkuenfte_jahr: float = 0.0      # Partner: Einkünfte (nach Werbungskosten/Rentenfreibetrag)
    partner_vorsorge_jahr: float = 0.0        # Partner: abziehbare Vorsorgeaufwendungen
    partner_wachstum: float = 0.02            # jährliche Steigerung der Partner-Einkünfte
    kinder_geburtsjahre: list = field(default_factory=list)
    kirchensteuer: float = 0.0                # 0, 0.08, 0.09
    kv_modus: str = "auto"                    # auto | kvdr | freiwillig
    gkv_anteil_zweite_haelfte: float = 95.0   # % der 2. Erwerbshälfte in GKV (9/10-Regel)
    luecken_kv_familienversichert: bool = False
    sonstige_einkuenfte_jahr: float = 0.0     # z. B. Mieten/Betriebsrente (voll steuerpflichtig)
    # Privatvermögen / Depot
    depot_start: float = 0.0
    depot_gewinnanteil: float = 0.3           # Anteil Kursgewinne am Depotwert (0..1)
    depot_rendite: float = 0.03               # Wertentwicklung p.a. (nominal, nach Kosten)
    depot_teilfreistellung: float = 0.3       # z. B. 30 % bei Aktienfonds

    @property
    def geburt(self) -> date:
        return date.fromisoformat(self.geburtsdatum)

    @property
    def verheiratet(self) -> bool:
        return self.steuerklasse in config.SPLITTING_KLASSEN

    @staticmethod
    def from_dict(d: dict) -> "Person":
        return Person(**_filter(Person, d))


@dataclass
class Annahmen:
    start: str = ""                           # ISO "YYYY-MM" Simulationsstart; leer = heute
    rentensteigerung: float = 0.02            # Rentenanpassung p.a. (1. Juli)
    inflation: float = 0.02
    lohnsteigerung: float = 0.03              # Gehalt, BBG, Durchschnittsentgelt
    tarif_indexierung: float = 0.02           # Fortschreibung Steuertarif/Freigrenzen
    zusatzbeitrag: float = config.ZUSATZBEITRAG_DURCHSCHNITT
    horizont_alter: int = 95

    def start_datum(self) -> date:
        if self.start:
            y, m = self.start.split("-")[:2]
            return date(int(y), int(m), 1)
        t = date.today()
        return date(t.year, t.month, 1)

    @staticmethod
    def from_dict(d: dict) -> "Annahmen":
        return Annahmen(**_filter(Annahmen, d))


@dataclass
class Szenario:
    name: str = "Szenario"
    erwerbsende_alter_m: int = 63 * 12        # Alter (Monate), mit dem die Haupttätigkeit endet
    rentenbeginn_alter_m: int = 63 * 12       # Alter (Monate) bei Rentenbeginn
    teilrente_prozent: float = 100.0          # Anteil der Rente, der zunächst bezogen wird
    vollrente_alter_m: int = 0                # 0 = kein späterer Wechsel auf Vollrente
    # Hinzuverdienst / Weiterarbeiten
    hinzuverdienst_monat: float = 0.0         # brutto/Monat (heutige Kaufkraft)
    hinzuverdienst_bis_alter_m: int = 67 * 12
    rv_aufstocken: bool = True                # RV-Beiträge entrichten (EP-Zuschläge)
    # Ausgleichszahlung § 187a SGB VI
    ausgleich_modus: str = "keine"            # keine | voll | betrag
    ausgleich_betrag: float = 0.0             # Gesamtbetrag (nominal) bei Modus "betrag"
    ausgleich_jahre: int = 1                  # Verteilung auf n Jahresraten vor Rentenbeginn
    # Überbrückung / Kapitalverzehr
    wunsch_netto_monat: float = 0.0           # Wunsch-Nettoeinkommen (heutige Kaufkraft); 0 = keine Entnahme
    entnahme_modus: str = "bis_rente"         # bis_rente | dauerhaft

    @staticmethod
    def from_dict(d: dict) -> "Szenario":
        return Szenario(**_filter(Szenario, d))


@dataclass
class Projekt:
    person: Person = field(default_factory=Person)
    annahmen: Annahmen = field(default_factory=Annahmen)
    szenarien: list = field(default_factory=list)

    def to_json(self) -> str:
        return json.dumps(
            {
                "person": asdict(self.person),
                "annahmen": asdict(self.annahmen),
                "szenarien": [asdict(s) for s in self.szenarien],
            },
            indent=2,
            ensure_ascii=False,
        )

    @staticmethod
    def from_json(text: str) -> "Projekt":
        d = json.loads(text)
        return Projekt(
            Person.from_dict(d.get("person", {})),
            Annahmen.from_dict(d.get("annahmen", {})),
            [Szenario.from_dict(s) for s in d.get("szenarien", [])],
        )


def standard_szenarien() -> list[Szenario]:
    return [
        Szenario(name="A: Rente mit 63 (Abschlag)", erwerbsende_alter_m=63 * 12, rentenbeginn_alter_m=63 * 12),
        Szenario(name="B: Rente mit 63 + Ausgleichszahlung", erwerbsende_alter_m=63 * 12,
                 rentenbeginn_alter_m=63 * 12, ausgleich_modus="voll", ausgleich_jahre=3),
        Szenario(name="C: Rente mit 65 (45 Jahre, abschlagsfrei)", erwerbsende_alter_m=65 * 12, rentenbeginn_alter_m=65 * 12),
        Szenario(name="E: Regulär mit 67", erwerbsende_alter_m=67 * 12, rentenbeginn_alter_m=67 * 12),
        Szenario(name="D: Aufhören mit 62, Rente mit 67 (Depot-Brücke)", erwerbsende_alter_m=62 * 12,
                 rentenbeginn_alter_m=67 * 12, wunsch_netto_monat=2_500.0, entnahme_modus="bis_rente"),
    ]


def standard_projekt() -> Projekt:
    p = Person(depot_start=250_000.0)
    return Projekt(p, Annahmen(), standard_szenarien())
