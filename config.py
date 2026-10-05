"""
config.py – Gesetzliche Stellschrauben (zentral, leicht wartbar).

Alle Werte beziehen sich auf das Basisjahr BASISJAHR (2026) und werden für
Folgejahre in ``engine`` über die Annahmen (Lohn-/Tarifindexierung,
Rentenanpassung) fortgeschrieben.

WICHTIG: Die Werte sind nach bestem Wissen gepflegt, aber jährlich zu prüfen
(Rentenanpassung 1.7., Bekanntmachung der Rechengrößen der Sozialversicherung,
Jahressteuergesetz). Stellen mit "PRÜFEN" sind besonders änderungsanfällig.
"""
from __future__ import annotations

BASISJAHR = 2026

# --------------------------------------------------------------------------
# Gesetzliche Rentenversicherung
# --------------------------------------------------------------------------
# Aktueller Rentenwert ab 1.7.2026 (€ je Entgeltpunkt, brutto/Monat). PRÜFEN.
RENTENWERT_AB_JULI_2026 = 42.52
RENTENANPASSUNG_MONAT = 7          # Anpassung jeweils zum 1. Juli

BEITRAGSSATZ_RV = 0.186            # Beitragssatz allgemeine RV (AN + AG je 9,3 %)
BBG_RV_JAHR = 101_400.0            # Beitragsbemessungsgrenze RV 2026 (West/Ost einheitlich)
DURCHSCHNITTSENTGELT_JAHR = 51_944.0   # vorläufiges Durchschnittsentgelt 2026. PRÜFEN.

# Altersgrenzen
RENTENABSCHLAG_PRO_MONAT = 0.003   # 0,3 % je Monat vorzeitiger Inanspruchnahme
MAX_ABSCHLAG_MONATE = 48           # 14,4 % Obergrenze (§ 77 SGB VI)
RENTENZUSCHLAG_PRO_MONAT = 0.005   # 0,5 % je Monat Rentenaufschub nach Regelaltersgrenze
FRUEHESTENS_LANGJAEHRIG_MONATE = 63 * 12   # frühester Beginn Altersrente für langjährig Versicherte
WARTEZEIT_LANGJAEHRIG = 35
WARTEZEIT_BESONDERS_LANGJAEHRIG = 45
WARTEZEIT_REGELALTER = 5
MIN_ALTER_AUSGLEICHSZAHLUNG = 50   # § 187a SGB VI: ab Vollendung 50. Lebensjahr


def regelaltersgrenze_monate(geburtsjahr: int) -> int:
    """Regelaltersgrenze (§ 35, § 235 SGB VI) in Lebensmonaten."""
    if geburtsjahr <= 1946:
        return 65 * 12
    if geburtsjahr <= 1958:                 # 1947: 65+1 … 1958: 65+12
        return 65 * 12 + (geburtsjahr - 1946)
    if geburtsjahr <= 1963:                 # 1959: 66+2 … 1963: 66+10
        return 66 * 12 + 2 * (geburtsjahr - 1958)
    return 67 * 12


def abschlagsfrei_besonders_langjaehrig_monate(geburtsjahr: int) -> int:
    """Abschlagsfreies Eintrittsalter Altersrente für besonders langjährig
    Versicherte (45 Jahre), § 236b SGB VI, in Lebensmonaten."""
    if geburtsjahr <= 1952:
        return 63 * 12
    if geburtsjahr <= 1963:                 # 1953: 63+2 … 1963: 64+10
        return 63 * 12 + 2 * (geburtsjahr - 1952)
    return 65 * 12


# --------------------------------------------------------------------------
# Krankenversicherung / Pflegeversicherung
# --------------------------------------------------------------------------
KV_ALLGEMEIN = 0.146               # allgemeiner Beitragssatz (je 7,3 % AN/AG bzw. Rentner/DRV)
KV_ERMAESSIGT = 0.140              # ohne Krankengeldanspruch (Vollrentner im Job)
ZUSATZBEITRAG_DURCHSCHNITT = 0.029 # durchschnittlicher Zusatzbeitrag 2026 (Default). PRÜFEN.
BBG_KV_JAHR = 69_750.0             # BBG KV/PV 2026
MINDESTBEMESSUNG_FREIW_KV_MONAT = 1_318.33   # 1/3 Bezugsgröße 2026 (freiwillige KV, Lücke)

PV_BEITRAGSSATZ = 0.036            # PV 2026 gesamt
PV_KINDERLOSENZUSCHLAG = 0.006     # nur Mitglieder ohne Kinder (ab 23 Jahren)
PV_ABSCHLAG_PRO_KIND = 0.0025      # ab 2. Kind unter 25 J., max. 4 Kinder (-1,0 %)
PV_ABSCHLAG_MAX_KINDER = 4         # 2.–5. Kind zählt
PV_KINDER_ALTERSGRENZE = 25

# Arbeitslosenversicherung (nur AN-Anteil, bis Regelaltersgrenze)
AV_BEITRAGSSATZ_AN = 0.013

# --------------------------------------------------------------------------
# Einkommensteuer
# --------------------------------------------------------------------------
# Tarif 2026 (§ 32a EStG)
GRUNDFREIBETRAG = 12_348.0
TARIF_ZONE2_BIS = 17_799.0
TARIF_ZONE3_BIS = 69_878.0
TARIF_ZONE4_BIS = 277_825.0
TARIF_Z2 = (914.51, 1_400.0)
TARIF_Z3 = (173.10, 2_397.0, 1_034.87)
TARIF_Z4 = (0.42, 11_135.63)
TARIF_Z5 = (0.45, 19_470.38)

SOLI_SATZ = 0.055
SOLI_MILDERUNG = 0.119
SOLI_FREIGRENZE = 20_350.0         # je Person (Splitting: doppelt). PRÜFEN.

# Pauschbeträge (nominal fix, werden NICHT indexiert)
WERBUNGSKOSTEN_PAUSCHBETRAG_RENTE = 102.0      # § 9a Nr. 3 EStG
WERBUNGSKOSTEN_PAUSCHBETRAG_AN = 1_230.0       # § 9a Nr. 1 EStG
SONDERAUSGABEN_PAUSCHBETRAG = 36.0             # Splitting: 72 €
SPARER_PAUSCHBETRAG = 1_000.0                  # Splitting: 2.000 €

# Vorsorgeaufwendungen
HOECHSTBETRAG_ALTERSVORSORGE = 30_826.0        # § 10 Abs. 3 EStG 2026 (Splitting: doppelt). PRÜFEN.
KV_ANTEIL_KRANKENGELD_ABZUG = 0.04             # 4 % Kürzung beim AN-Beitrag (Krankengeldanspruch)

# Abgeltungsteuer auf Depot-Entnahmen
ABGELTUNGSTEUER_SATZ = 0.25
# (+ Soli 5,5 %) -> 26,375 % ; Kirchensteuer ignoriert

# Besteuerungsanteil Renten (§ 22 Nr. 1 S. 3 a) aa) EStG i.d.F. Wachstumschancengesetz)
def besteuerungsanteil(rentenbeginn_jahr: int) -> float:
    """Besteuerungsanteil in Prozent nach Jahr des Rentenbeginns.

    bis 2005: 50 %; 2006–2020: +2 %-Punkte p.a. (2020: 80 %); 2021/2022: +1 (82 %);
    ab 2023: +0,5 %-Punkte p.a. (2023: 83 %, 2025: 84 %, 2026: 84,5 %), 100 % ab 2058.
    """
    j = rentenbeginn_jahr
    if j <= 2005:
        return 50.0
    if j <= 2020:
        return 50.0 + 2.0 * (j - 2005)
    if j <= 2022:
        return 80.0 + 1.0 * (j - 2020)
    return min(100.0, 83.0 + 0.5 * (j - 2023))


# Steuerklassen -> Veranlagungsart.
# Die Steuerklasse beeinflusst nur den monatlichen Lohnsteuerabzug (und das
# Elterngeld etc.). Die endgültige Steuerlast folgt aus der Veranlagung:
# Klasse 3/4/4F/5 = verheiratet (Zusammenveranlagung, Splittingtarif),
# Klasse 1 = Grundtarif.
STEUERKLASSEN = {
    "1": "Klasse 1 (ledig/getrennt) – Grundtarif",
    "3": "Klasse 3 (verheiratet, Partner Klasse 5) – Splittingtarif",
    "4": "Klasse 4 (verheiratet) – Splittingtarif",
    "4F": "Klasse 4 mit Faktor (verheiratet) – Splittingtarif",
    "5": "Klasse 5 (verheiratet, Partner Klasse 3) – Splittingtarif",
}
SPLITTING_KLASSEN = {"3", "4", "4F", "5"}

# --------------------------------------------------------------------------
# Sonstiges
# --------------------------------------------------------------------------
MINIJOB_GRENZE_MONAT = 603.0       # 2026 (Mindestlohn 13,90 €)
RV_ANTEIL_AN = BEITRAGSSATZ_RV / 2


def parameter_tabelle() -> list[tuple[str, str, str]]:
    """Übersicht für die GUI: (Gruppe, Parameter, Wert)."""
    return [
        ("Rente", "Rentenwert ab 1.7.2026", f"{RENTENWERT_AB_JULI_2026:.2f} €"),
        ("Rente", "Beitragssatz RV", f"{BEITRAGSSATZ_RV:.1%}"),
        ("Rente", "BBG RV (Jahr)", f"{BBG_RV_JAHR:,.0f} €"),
        ("Rente", "Durchschnittsentgelt (Jahr)", f"{DURCHSCHNITTSENTGELT_JAHR:,.0f} €"),
        ("Rente", "Abschlag je Monat", f"{RENTENABSCHLAG_PRO_MONAT:.1%} (max. {MAX_ABSCHLAG_MONATE * RENTENABSCHLAG_PRO_MONAT:.1%})"),
        ("Rente", "Zuschlag je Monat Aufschub", f"{RENTENZUSCHLAG_PRO_MONAT:.1%}"),
        ("KV/PV", "KV allgemein", f"{KV_ALLGEMEIN:.1%}"),
        ("KV/PV", "Zusatzbeitrag (Default)", f"{ZUSATZBEITRAG_DURCHSCHNITT:.1%}"),
        ("KV/PV", "BBG KV/PV (Jahr)", f"{BBG_KV_JAHR:,.2f} €"),
        ("KV/PV", "PV-Beitragssatz", f"{PV_BEITRAGSSATZ:.1%}"),
        ("KV/PV", "PV Kinderlosenzuschlag", f"{PV_KINDERLOSENZUSCHLAG:.1%}"),
        ("KV/PV", "PV-Abschlag je Kind (2.–5.) unter 25", f"{PV_ABSCHLAG_PRO_KIND:.2%}"),
        ("Steuer", "Grundfreibetrag", f"{GRUNDFREIBETRAG:,.0f} €"),
        ("Steuer", "Zonen (Obergrenzen)", f"{TARIF_ZONE2_BIS:,.0f} / {TARIF_ZONE3_BIS:,.0f} / {TARIF_ZONE4_BIS:,.0f} €"),
        ("Steuer", "WK-Pauschbetrag Rente", f"{WERBUNGSKOSTEN_PAUSCHBETRAG_RENTE:.0f} €"),
        ("Steuer", "WK-Pauschbetrag Arbeitnehmer", f"{WERBUNGSKOSTEN_PAUSCHBETRAG_AN:,.0f} €"),
        ("Steuer", "Sonderausgaben-Pauschbetrag", f"{SONDERAUSGABEN_PAUSCHBETRAG:.0f} € (Splitting 72 €)"),
        ("Steuer", "Höchstbetrag Altersvorsorge", f"{HOECHSTBETRAG_ALTERSVORSORGE:,.0f} €"),
        ("Steuer", "Besteuerungsanteil Rentenbeginn 2026", f"{besteuerungsanteil(2026):.1f} %"),
        ("Steuer", "Besteuerungsanteil Rentenbeginn 2030", f"{besteuerungsanteil(2030):.1f} %"),
        ("Steuer", "Solidaritätszuschlag Freigrenze", f"{SOLI_FREIGRENZE:,.0f} €"),
        ("Steuer", "Abgeltungsteuer inkl. Soli", f"{ABGELTUNGSTEUER_SATZ * (1 + SOLI_SATZ):.3%}"),
        ("Sonstiges", "Minijob-Grenze", f"{MINIJOB_GRENZE_MONAT:.0f} €/Monat"),
    ]
