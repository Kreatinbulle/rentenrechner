"""
engine.py – Finanzmathematische Berechnung (ohne UI-Abhängigkeit).

Ablauf von ``simuliere``:
  1. Rentenanspruch (Rentenart, Wartezeiten, Zugangsfaktor) und Rentenhöhe
  2. Ausgleichszahlung nach § 187a SGB VI (Kosten, zusätzliche Entgeltpunkte)
  3. Monatsschleife je Kalenderjahr: Gehalt, Rente, Hinzuverdienst, Sozialabgaben
  4. Jahresabschluss: Einkommensteuer des Haushalts (Splitting, Inkrementalmethode),
     Verteilung der Steuer auf die Monate
  5. Depot-Entnahmen (Überbrückung) mit Abgeltungsteuer
  6. Kumulierung (nominal / real) für den Szenarienvergleich

Zeitachse: Monatsindex ``i = Jahr*12 + (Monat-1)``. Alle Beträge nominal; Reale Werte
werden über den Deflator (Inflation seit Simulationsstart) berechnet.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

import config as C
from models import Annahmen, Person, Szenario
from tracer import Tracer

BASIS_NAMEN = {
    1: "Nur Rente (netto, abzgl. Ausgleichszahlung)",
    2: "Verfügbares Netto-Einkommen (Gehalt, Rente, Hinzuverdienst, Depot-Entnahmen)",
    3: "Gesamtposition (wie 2 + Depot-Wertveränderung)",
}


# ============================================================================
# Hilfsfunktionen
# ============================================================================
def midx(jahr: int, monat: int) -> int:
    return jahr * 12 + monat - 1


def fmt_alter(monate: float) -> str:
    m = int(round(monate))
    return f"{m // 12} J. {m % 12} M."


def lohn_index(jahr: int, ann: Annahmen) -> float:
    return (1 + ann.lohnsteigerung) ** max(0, jahr - C.BASISJAHR)


def tarif_index(jahr: int, ann: Annahmen) -> float:
    return (1 + ann.tarif_indexierung) ** max(0, jahr - C.BASISJAHR)


def rentenwert(i: int, ann: Annahmen) -> float:
    """Aktueller Rentenwert im Monat i (Anpassung jeweils 1. Juli)."""
    jahr, monat = i // 12, i % 12 + 1
    n = jahr - C.BASISJAHR - (1 if monat < C.RENTENANPASSUNG_MONAT else 0)
    return C.RENTENWERT_AB_JULI_2026 * (1 + ann.rentensteigerung) ** max(0, n)


# ============================================================================
# Einkommensteuer
# ============================================================================
def _tarif_basis(x: float) -> float:
    """§ 32a EStG (Parameter 2026), x = zvE (Grundtarif) in €."""
    if x <= C.GRUNDFREIBETRAG:
        return 0.0
    if x <= C.TARIF_ZONE2_BIS:
        y = (x - C.GRUNDFREIBETRAG) / 10_000
        return (C.TARIF_Z2[0] * y + C.TARIF_Z2[1]) * y
    if x <= C.TARIF_ZONE3_BIS:
        z = (x - C.TARIF_ZONE2_BIS) / 10_000
        return (C.TARIF_Z3[0] * z + C.TARIF_Z3[1]) * z + C.TARIF_Z3[2]
    if x <= C.TARIF_ZONE4_BIS:
        return C.TARIF_Z4[0] * x - C.TARIF_Z4[1]
    return C.TARIF_Z5[0] * x - C.TARIF_Z5[1]


def einkommensteuer(zve: float, splitting: bool, f: float = 1.0) -> float:
    """Tarifliche ESt. ``f`` = Indexierungsfaktor (Tarif wird mit f skaliert)."""
    x = math.floor(max(0.0, zve))
    if splitting:
        return 2 * math.floor(f * _tarif_basis(x / 2 / f))
    return math.floor(f * _tarif_basis(x / f))


def solidaritaetszuschlag(est: float, splitting: bool, f: float = 1.0) -> float:
    fg = C.SOLI_FREIGRENZE * f * (2 if splitting else 1)
    if est <= fg:
        return 0.0
    return min(C.SOLI_SATZ * est, C.SOLI_MILDERUNG * (est - fg))


def steuer_gesamt(zve: float, splitting: bool, f: float, kist_satz: float) -> dict:
    est = einkommensteuer(zve, splitting, f)
    soli = solidaritaetszuschlag(est, splitting, f)
    kist = est * kist_satz
    return {"zve": max(0.0, zve), "est": est, "soli": soli, "kist": kist, "summe": est + soli + kist}


# ============================================================================
# Rentenanspruch
# ============================================================================
@dataclass
class Anspruch:
    ok: bool
    art: str
    zugangsfaktor: float
    abschlag_monate: int = 0
    zuschlag_monate: int = 0
    hinweise: list = field(default_factory=list)


def rentenanspruch(geburtsjahr: int, alter_m: int, jahre35: float, jahre45: float) -> Anspruch:
    rag = C.regelaltersgrenze_monate(geburtsjahr)
    bl = C.abschlagsfrei_besonders_langjaehrig_monate(geburtsjahr)
    if alter_m >= rag:
        if jahre35 < C.WARTEZEIT_REGELALTER:
            return Anspruch(False, "–", 0, hinweise=["Wartezeit 5 Jahre nicht erfüllt."])
        zm = alter_m - rag
        art = "Regelaltersrente" if zm == 0 else "Regelaltersrente (mit Rentenaufschub)"
        return Anspruch(True, art, 1 + C.RENTENZUSCHLAG_PRO_MONAT * zm, zuschlag_monate=zm)
    hinweise: list[str] = []
    if jahre45 >= C.WARTEZEIT_BESONDERS_LANGJAEHRIG and alter_m >= bl:
        return Anspruch(True, "Altersrente für besonders langjährig Versicherte", 1.0)
    if jahre45 >= C.WARTEZEIT_BESONDERS_LANGJAEHRIG:
        hinweise.append(
            f"45 Jahre erfüllt, aber abschlagsfreies Alter erst {fmt_alter(bl)} – "
            "früher nur als Rente für langjährig Versicherte mit Abschlag."
        )
    if jahre35 >= C.WARTEZEIT_LANGJAEHRIG and alter_m >= C.FRUEHESTENS_LANGJAEHRIG_MONATE:
        am = min(C.MAX_ABSCHLAG_MONATE, rag - alter_m)
        return Anspruch(True, "Altersrente für langjährig Versicherte (vorzeitig)",
                        1 - C.RENTENABSCHLAG_PRO_MONAT * am, abschlag_monate=am, hinweise=hinweise)
    if jahre35 < C.WARTEZEIT_LANGJAEHRIG:
        hinweise.append(f"Wartezeit 35 Jahre nicht erfüllt ({jahre35:.1f} Jahre bei Rentenbeginn).")
    if alter_m < C.FRUEHESTENS_LANGJAEHRIG_MONATE:
        hinweise.append("Frühester Rentenbeginn für langjährig Versicherte: 63 Jahre.")
    return Anspruch(False, "–", 0, hinweise=hinweise)


# ============================================================================
# Pflegeversicherung
# ============================================================================
def pv_saetze(p: Person, jahr: int) -> dict:
    """Gesamt-PV-Satz für Rentner (trägt allein) und AN-Anteil im Arbeitsverhältnis."""
    kinder = list(p.kinder_geburtsjahre)
    unter25 = sum(1 for g in kinder if jahr - int(g) < C.PV_KINDER_ALTERSGRENZE)
    zuschlag = C.PV_KINDERLOSENZUSCHLAG if len(kinder) == 0 else 0.0
    abschlag = C.PV_ABSCHLAG_PRO_KIND * min(max(unter25 - 1, 0), C.PV_ABSCHLAG_MAX_KINDER)
    voll = C.PV_BEITRAGSSATZ + zuschlag - abschlag
    an = C.PV_BEITRAGSSATZ / 2 + zuschlag - abschlag
    return {"voll": voll, "an": an, "zuschlag": zuschlag, "abschlag": abschlag, "kinder_u25": unter25}


def kv_modus_effektiv(p: Person) -> tuple[str, str]:
    """KVdR-Prüfung (9/10-Regel). Gibt (modus, begründung)."""
    ok = p.gkv_anteil_zweite_haelfte >= 90.0
    if p.kv_modus == "kvdr":
        return "kvdr", "KVdR vorgegeben."
    if p.kv_modus == "freiwillig":
        return "freiwillig", "Freiwillige GKV vorgegeben."
    if ok:
        return "kvdr", f"9/10-Regel erfüllt ({p.gkv_anteil_zweite_haelfte:.0f} % ≥ 90 %) → KVdR-Pflichtversicherung."
    return "freiwillig", f"9/10-Regel NICHT erfüllt ({p.gkv_anteil_zweite_haelfte:.0f} % < 90 %) → freiwillige GKV."


# ============================================================================
# Ergebnis-Container
# ============================================================================
@dataclass
class Ergebnis:
    szenario: Szenario
    ok: bool
    fehler: list = field(default_factory=list)
    warnungen: list = field(default_factory=list)
    monat: pd.DataFrame = field(default_factory=pd.DataFrame)
    jahr: pd.DataFrame = field(default_factory=pd.DataFrame)
    trace: Tracer = field(default_factory=Tracer)
    kennzahlen: dict = field(default_factory=dict)


# ============================================================================
# Jahressteuer (Haushalt, Inkrementalmethode)
# ============================================================================
def _jahressteuer(p: Person, ann: Annahmen, jahr: int, y0: int, d: dict, ausgleich: float) -> dict:
    """Berechnet die der Person zuzurechnende Steuer eines (ggf. annualisierten) Jahres.

    d: rente_stpfl, lohn_brutto, sonstige, kvpv_basis, rv_an (RV-Beitrag AN), rv_ag, s (Annualisierungsfaktor)
    """
    f = tarif_index(jahr, ann)
    lf = lohn_index(jahr, ann)
    split = p.verheiratet
    sa_pb = C.SONDERAUSGABEN_PAUSCHBETRAG * (2 if split else 1)
    wk_rente = min(d["rente_stpfl"], C.WERBUNGSKOSTEN_PAUSCHBETRAG_RENTE) if d["rente_stpfl"] > 0 else 0.0
    wk_an = min(d["lohn_brutto"], C.WERBUNGSKOSTEN_PAUSCHBETRAG_AN) if d["lohn_brutto"] > 0 else 0.0
    cap = C.HOECHSTBETRAG_ALTERSVORSORGE * lf * (2 if split else 1)
    av_gesamt = d["rv_an"] + d["rv_ag"] + ausgleich
    av_abzug = max(0.0, min(av_gesamt, cap) - d["rv_ag"])
    person_zve = (d["rente_stpfl"] - wk_rente) + (d["lohn_brutto"] - wk_an) + d["sonstige"] \
        - d["kvpv_basis"] - av_abzug
    if split:
        g = (1 + p.partner_wachstum) ** (jahr - y0)
        partner = p.partner_einkuenfte_jahr * g - p.partner_vorsorge_jahr * g
    else:
        partner = 0.0
    total = steuer_gesamt(person_zve + partner - sa_pb, split, f, p.kirchensteuer)
    basis = steuer_gesamt(partner - sa_pb, split, f, p.kirchensteuer) if split else steuer_gesamt(0, False, f, 0)
    return {
        "wk_rente": wk_rente, "wk_an": wk_an, "av_abzug": av_abzug, "sa_pb": sa_pb,
        "person_zve": person_zve, "partner": partner,
        "haushalt": total, "basis": basis,
        "zusatz": total["summe"] - basis["summe"],
        "zusatz_est": total["est"] - basis["est"],
        "zusatz_soli": total["soli"] - basis["soli"],
        "zusatz_kist": total["kist"] - basis["kist"],
    }


# ============================================================================
# Hauptsimulation
# ============================================================================
def simuliere(p: Person, ann: Annahmen, sz: Szenario) -> Ergebnis:  # noqa: C901 (bewusst linear/nachvollziehbar)
    tr = Tracer()
    res = Ergebnis(szenario=sz, ok=True, trace=tr)
    geb = p.geburt
    gj = geb.year
    birth_idx = midx(geb.year, geb.month)
    start = ann.start_datum()
    start_idx = midx(start.year, start.month)
    y0 = start.year
    end_idx = birth_idx + ann.horizont_alter * 12          # exklusiv
    if end_idx <= start_idx:
        res.ok = False
        res.fehler.append("Horizont liegt vor dem Simulationsstart.")
        return res

    rag = C.regelaltersgrenze_monate(gj)
    bl = C.abschlagsfrei_besonders_langjaehrig_monate(gj)
    tr.add("Rentenanspruch", "Regelaltersgrenze", f"Jahrgang {gj}", fmt_alter(rag), "", "§ 35, § 235 SGB VI")
    tr.add("Rentenanspruch", "Abschlagsfreies Alter besonders langjährig (45 J.)", f"Jahrgang {gj}",
           fmt_alter(bl), "", "§ 236b SGB VI")

    # --- Zeitpunkte ------------------------------------------------------
    ew_alter = min(sz.erwerbsende_alter_m, sz.rentenbeginn_alter_m)
    if sz.erwerbsende_alter_m > sz.rentenbeginn_alter_m:
        res.warnungen.append("Erwerbsende nach Rentenbeginn: Erwerbsende wurde auf Rentenbeginn gesetzt "
                             "(Weiterarbeiten bitte über 'Hinzuverdienst' abbilden).")
    ew_idx = birth_idx + ew_alter + 1          # erster Monat OHNE Haupterwerb
    rb_idx = birth_idx + sz.rentenbeginn_alter_m + 1
    rb_jahr = rb_idx // 12
    voll_idx = birth_idx + sz.vollrente_alter_m + 1 if sz.vollrente_alter_m > sz.rentenbeginn_alter_m else None
    teil = min(max(sz.teilrente_prozent, 0.0), 100.0) / 100
    if voll_idx is None and teil < 1:
        res.warnungen.append("Teilrente ohne späteren Wechsel auf Vollrente: der nicht bezogene Rentenanteil "
                             "verfällt in dieser Simulation.")
    if rb_idx <= start_idx:
        res.ok = False
        res.fehler.append("Rentenbeginn liegt vor/zum Simulationsstart – bitte späteren Rentenbeginn wählen.")
        return res

    # --- Wartezeiten & EP -----------------------------------------------
    arbeitsmonate = max(0, ew_idx - start_idx)
    ep_neu = p.ep_pro_jahr * arbeitsmonate / 12
    ep_gesamt = p.ep_aktuell + ep_neu
    j35 = p.wartezeit_jahre_35 + arbeitsmonate / 12
    j45 = p.wartezeit_jahre_45 + arbeitsmonate / 12
    tr.add("Rentenanspruch", "Weitere Erwerbsmonate bis Erwerbsende", f"{fmt_alter(ew_alter)} − heute", arbeitsmonate, "Monate")
    tr.add("Rentenanspruch", "Entgeltpunkte bei Rentenbeginn",
           f"{p.ep_aktuell:.3f} + {p.ep_pro_jahr:.3f} × {arbeitsmonate}/12", round(ep_gesamt, 4), "EP")
    tr.add("Rentenanspruch", "Wartezeit 35 / 45 Jahre bei Rentenbeginn", f"{p.wartezeit_jahre_35:.1f}/{p.wartezeit_jahre_45:.1f} + Erwerbsjahre",
           f"{j35:.1f} / {j45:.1f}", "Jahre")

    a1 = rentenanspruch(gj, sz.rentenbeginn_alter_m, j35, j45)
    if not a1.ok:
        res.ok = False
        res.fehler.append(f"Rentenbeginn mit {fmt_alter(sz.rentenbeginn_alter_m)} nicht möglich. " + " ".join(a1.hinweise))
        return res
    res.warnungen += a1.hinweise
    tr.add("Rentenanspruch", "Rentenart", "automatisch aus Alter und Wartezeiten", a1.art)
    if a1.abschlag_monate:
        tr.add("Rentenanspruch", "Abschlag", f"min({C.MAX_ABSCHLAG_MONATE}, RAG − Alter) = {a1.abschlag_monate} Monate × {C.RENTENABSCHLAG_PRO_MONAT:.1%}",
               round(a1.abschlag_monate * C.RENTENABSCHLAG_PRO_MONAT * 100, 2), "%", "§ 77 SGB VI, max. 14,4 %")
    if a1.zuschlag_monate:
        tr.add("Rentenanspruch", "Zuschlag Rentenaufschub", f"{a1.zuschlag_monate} Monate × {C.RENTENZUSCHLAG_PRO_MONAT:.1%}",
               round(a1.zuschlag_monate * C.RENTENZUSCHLAG_PRO_MONAT * 100, 2), "%")
    tr.add("Rentenanspruch", "Zugangsfaktor Rentenbeginn", "1 − 0,003 × Abschlagsmonate (+ 0,005 × Aufschubmonate)", round(a1.zugangsfaktor, 4))
    if sz.rentenbeginn_alter_m < rag and sz.rentenbeginn_alter_m >= bl and j45 < 45:
        res.warnungen.append(f"45 Beitragsjahre bei Rentenbeginn nicht erreicht ({j45:.1f}) – abschlagsfreie Rente für besonders langjährig Versicherte nicht möglich.")

    ep1 = ep_gesamt * (teil if (voll_idx or teil < 1) else 1.0)
    ep1_unbewertet = ep1
    ep2 = 0.0
    a2 = None
    if voll_idx is not None and teil < 1:
        a2 = rentenanspruch(gj, sz.vollrente_alter_m, j35, j45)
        if not a2.ok:
            res.ok = False
            res.fehler.append(f"Wechsel auf Vollrente mit {fmt_alter(sz.vollrente_alter_m)} nicht möglich. " + " ".join(a2.hinweise))
            return res
        ep2 = ep_gesamt * (1 - teil)
        tr.add("Rentenhöhe", "Teilrente → Vollrente", f"{teil:.0%} ab Rentenbeginn, Rest ab {fmt_alter(sz.vollrente_alter_m)}; "
               f"Zugangsfaktor 2. Teil {a2.zugangsfaktor:.4f}", round(ep2, 4), "EP (2. Teil)")

    # --- Ausgleichszahlung § 187a ----------------------------------------
    zahlungen: dict[int, float] = {}        # Jahr -> Betrag (Dezember)
    ep_ausgleich = 0.0
    if sz.ausgleich_modus != "keine":
        if a1.abschlag_monate == 0:
            res.warnungen.append("Ausgleichszahlung gewählt, aber kein Rentenabschlag vorhanden – Zahlung entfällt.")
        else:
            ep_ziel = ep1_unbewertet * (1 - a1.zugangsfaktor)
            n = max(1, int(sz.ausgleich_jahre))
            jahre = [y for y in range(rb_jahr - n, rb_jahr)
                     if y >= y0 and (y - geb.year) * 12 + 11 >= C.MIN_ALTER_AUSGLEICHSZAHLUNG * 12]
            if jahre and len(jahre) < n:
                res.warnungen.append(f"Nur {len(jahre)} von {n} Zahlungsjahren möglich (Zahlung frühestens im Simulationsstart-Jahr und "
                                     "ab Alter 50, spätestens im Jahr vor Rentenbeginn).")
            if not jahre:
                res.warnungen.append("Keine zulässigen Zahlungsjahre (ab Alter 50 und ab Simulationsstart) – keine Ausgleichszahlung.")
            else:
                preis = {y: C.DURCHSCHNITTSENTGELT_JAHR * lohn_index(y, ann) * C.BEITRAGSSATZ_RV for y in jahre}
                if sz.ausgleich_modus == "voll":
                    ep_je = ep_ziel / len(jahre)
                    zahlungen = {y: ep_je * preis[y] for y in jahre}
                    ep_ausgleich = ep_ziel
                else:
                    rate = sz.ausgleich_betrag / len(jahre)
                    ep_roh = sum(rate / preis[y] for y in jahre)
                    k = min(1.0, ep_ziel / ep_roh) if ep_roh > 0 else 0
                    zahlungen = {y: rate * k for y in jahre}
                    ep_ausgleich = ep_roh * k
                    if k < 1:
                        res.warnungen.append("Eingegebener Ausgleichsbetrag übersteigt den vollen Ausgleich – auf Maximum gekürzt.")
                tr.add("Ausgleichszahlung", "EP-Verlust durch Abschlag", f"{ep1_unbewertet:.4f} × (1 − {a1.zugangsfaktor:.4f})", round(ep_ziel, 4), "EP", "§ 187a Abs. 1 SGB VI")
                tr.add("Ausgleichszahlung", "Preis je EP (Zahlungsjahr, Beispiel)", f"Durchschnittsentgelt × Beitragssatz = {C.DURCHSCHNITTSENTGELT_JAHR:,.0f} × {C.BEITRAGSSATZ_RV:.1%}",
                       round(C.DURCHSCHNITTSENTGELT_JAHR * C.BEITRAGSSATZ_RV, 2), "€/EP", "§ 187a Abs. 2 SGB VI (Näherung)")
                for y, b in zahlungen.items():
                    tr.add("Ausgleichszahlung", f"Zahlung {y}", "EP-Anteil × Preis je EP", round(b, 2), "€")
                tr.add("Ausgleichszahlung", "Gekaufte Entgeltpunkte gesamt", "Σ Zahlung / Preis", round(ep_ausgleich, 4), "EP",
                       f"= {ep_ausgleich / max(ep_ziel, 1e-9):.0%} des Abschlags ausgeglichen")
    ep1_bewertet = ep1 * a1.zugangsfaktor + ep_ausgleich
    ep2_bewertet = ep2 * (a2.zugangsfaktor if a2 else 0)
    rw_start = rentenwert(rb_idx, ann)
    tr.add("Rentenhöhe", "Persönliche Entgeltpunkte (1. Teil)", f"EP × ZF + Ausgleich-EP = {ep1:.4f} × {a1.zugangsfaktor:.4f} + {ep_ausgleich:.4f}",
           round(ep1_bewertet, 4), "EP")
    tr.add("Rentenhöhe", f"Rentenwert zum Rentenbeginn ({rb_idx // 12}-{rb_idx % 12 + 1:02d})",
           f"{C.RENTENWERT_AB_JULI_2026} × (1+{ann.rentensteigerung:.1%})^Anpassungen", round(rw_start, 3), "€/EP")
    tr.add("Rentenhöhe", "Monatsrente brutto bei Rentenbeginn", "pers. EP × Rentenwert × Rentenartfaktor (1,0)",
           round(ep1_bewertet * rw_start, 2), "€/Monat", "§ 64 SGB VI")

    # --- Krankenversicherung --------------------------------------------
    kv_mod, kv_grund = kv_modus_effektiv(p)
    tr.add("Sozialabgaben", "KV-Modus im Ruhestand", "9/10-Regel § 5 Abs. 1 Nr. 11 SGB V", kv_mod, "", kv_grund)
    z = ann.zusatzbeitrag
    kv_rentner_satz = C.KV_ALLGEMEIN / 2 + z / 2
    tr.add("Sozialabgaben", "KV-Satz Rentner (Eigenanteil)", f"({C.KV_ALLGEMEIN:.1%} + {z:.2%}) / 2 – DRV trägt die andere Hälfte", round(kv_rentner_satz * 100, 3), "%")

    # ---------------------------------------------------------------------
    # Zustände der Schleife
    # ---------------------------------------------------------------------
    rows: list[dict] = []
    jahr_rows: list[dict] = []
    ep_zus: list[tuple[int, float]] = []    # (wirksam ab Monatsindex, EP)
    depot = p.depot_start
    basis_kosten = p.depot_start * (1 - p.depot_gewinnanteil)
    abg_satz = C.ABGELTUNGSTEUER_SATZ * (1 + C.SOLI_SATZ)
    freibetrag_rente: float | None = None
    ba = C.besteuerungsanteil(rb_jahr)
    tr.add("Rentenbesteuerung", f"Besteuerungsanteil (Rentenbeginn {rb_jahr})", "50 % + 2 %/J. bis 2020, +1 %/J. bis 2022, +0,5 %/J. ab 2023",
           ba, "%", "§ 22 Nr. 1 S. 3 a) aa) EStG i.d.F. Wachstumschancengesetz")

    erste_jahr, letzte_jahr = start_idx // 12, (end_idx - 1) // 12
    for jahr in range(erste_jahr, letzte_jahr + 1):
        monate = [i for i in range(max(start_idx, jahr * 12), min(end_idx, jahr * 12 + 12))]
        n_m = len(monate)
        s = 12 / n_m if n_m < 12 and (jahr == erste_jahr or jahr == letzte_jahr) else 1.0
        lf = lohn_index(jahr, ann)
        pv = pv_saetze(p, jahr)
        bbg_rv_m = C.BBG_RV_JAHR * lf / 12
        bbg_kv_m = C.BBG_KV_JAHR * lf / 12
        de = C.DURCHSCHNITTSENTGELT_JAHR * lf
        mini_grenze = C.MINIJOB_GRENZE_MONAT * lf
        mrows = []
        for i in monate:
            mo = i % 12 + 1
            age_m = i - birth_idx
            defl = (1 + ann.inflation) ** ((i - start_idx) / 12)
            r = {"i": i, "jahr": jahr, "monat": mo, "alter_m": age_m, "alter": age_m / 12, "deflator": defl}
            # Gehalt
            gehalt = p.brutto_jahr / 12 * lf if i < ew_idx else 0.0
            # Rente
            rw = rentenwert(i, ann)
            rente = 0.0
            if i >= rb_idx:
                rente += ep1_bewertet * rw
                if voll_idx is not None and i >= voll_idx:
                    rente += ep2_bewertet * rw
                rente += sum(e for eff, e in ep_zus if eff <= i) * rw
            sonstige = p.sonstige_einkuenfte_jahr / 12 * defl
            # Hinzuverdienst
            hz = 0.0
            if sz.hinzuverdienst_monat > 0 and ew_idx <= i < birth_idx + sz.hinzuverdienst_bis_alter_m + 1:
                hz = sz.hinzuverdienst_monat * defl
            minijob = 0 < hz <= mini_grenze
            hz_payroll = hz if hz > mini_grenze else 0.0
            wage = gehalt + hz_payroll
            # --- Sozialabgaben auf Arbeitsentgelt -------------------------
            rv_an = av_an = kv_an = pv_an = 0.0
            rv_pflicht = False
            if wage > 0:
                teil_aktiv = i >= rb_idx and teil < 1 and (voll_idx is None or i < voll_idx)
                rv_pflicht = (i < rb_idx) or teil_aktiv or sz.rv_aufstocken
                if i >= rb_idx and age_m >= rag and not sz.rv_aufstocken:
                    rv_pflicht = False
                if rv_pflicht:
                    rv_an = min(wage, bbg_rv_m) * C.RV_ANTEIL_AN
                if age_m < rag:
                    av_an = min(wage, bbg_rv_m) * C.AV_BEITRAGSSATZ_AN
                kv_satz = C.KV_ERMAESSIGT if (i >= rb_idx and not teil_aktiv) else C.KV_ALLGEMEIN
                kv_basis = min(wage, max(0.0, bbg_kv_m - rente))
                kv_an = kv_basis * (kv_satz / 2 + z / 2)
                pv_an = kv_basis * pv["an"]
            if hz_payroll > 0 and rv_pflicht:      # EP nur aus Hinzuverdienst (Gehalt steckt in ep_pro_jahr)
                ep_zus.append((rb_idx if i < rb_idx else midx(jahr + 1, 7),
                               min(hz_payroll, bbg_rv_m) / (de / 12)))   # EP = Monatsentgelt / (Durchschnittsentgelt/12)
            # --- KV/PV auf Rente ---------------------------------------
            kv_r = pv_r = zus_kv = zus_pv = 0.0
            if rente > 0:
                rbase = min(rente, bbg_kv_m)
                if kv_mod == "kvdr":
                    kv_r = rbase * kv_rentner_satz
                    pv_r = rbase * pv["voll"]
                else:
                    base = min(rente + sonstige, bbg_kv_m)
                    zus_kv = rbase * (C.KV_ALLGEMEIN / 2 + z / 2)
                    zus_pv = rbase * C.PV_BEITRAGSSATZ / 2
                    kv_r = base * (C.KV_ALLGEMEIN + z) - zus_kv
                    pv_r = base * pv["voll"] - zus_pv
            # --- Lücken-KV (kein Gehalt, keine Rente) --------------------
            kv_luecke = 0.0
            if ew_idx <= i < rb_idx and wage == 0 and not p.luecken_kv_familienversichert:
                base = min(max(C.MINDESTBEMESSUNG_FREIW_KV_MONAT * lf, sonstige + (hz if minijob else 0.0)), bbg_kv_m)
                kv_luecke = base * (C.KV_ALLGEMEIN + z + pv["voll"])
            r.update(gehalt_brutto=gehalt, rente_brutto=rente, hinz_brutto=hz, hinz_minijob=minijob,
                     sonstige_brutto=sonstige, rv_an=rv_an, av_an=av_an, kv_an=kv_an, pv_an=pv_an,
                     rv_pflicht=rv_pflicht, rente_kv=kv_r, rente_pv=pv_r, zuschuss_kv=zus_kv, zuschuss_pv=zus_pv,
                     luecken_kv=kv_luecke, wage=wage)
            mrows.append(r)

        # ------------------- Jahresaggregation & Steuer -------------------
        sm = lambda k: sum(x[k] for x in mrows)   # noqa: E731
        rente_y = sm("rente_brutto")
        wage_y = sm("wage")
        if rente_y > 0:
            if jahr == rb_jahr:
                freibetrag_rente = math.ceil((1 - ba / 100) * rente_y * s)
                fb_text = "1. Bezugsjahr: Freibetrag = (1 − Besteuerungsanteil) × Jahresrente, aufgerundet"
            elif jahr == rb_jahr + 1:
                freibetrag_rente = math.ceil((1 - ba / 100) * rente_y * s)
                fb_text = "2. Bezugsjahr: Rentenfreibetrag wird einmalig festgeschrieben (§ 22 Nr. 1 S. 3 a) bb) EStG)"
                tr.add("Rentenbesteuerung", "Festgeschriebener Rentenfreibetrag", f"aufrunden((1 − {ba:.1f} %) × Jahresrente {jahr} {rente_y * s:,.2f} €)",
                       freibetrag_rente, "€", "gilt ab jetzt unverändert – Rentenerhöhungen sind zu 100 % steuerpflichtig")
            else:
                fb_text = "Rentenfreibetrag unverändert (Dynamisierung 100 % steuerpflichtig)"
            if freibetrag_rente is None:     # Rentenbeginn lag vor dem Start (nicht möglich), Absicherung
                freibetrag_rente = math.ceil((1 - ba / 100) * rente_y * s)
                fb_text = "Freibetrag aus erstem simuliertem Rentenjahr"
        else:
            fb_text = "keine Rente"
        stpfl_rente = max(0.0, rente_y * s - (freibetrag_rente or 0.0)) if rente_y > 0 else 0.0
        sonst_y = sm("sonstige_brutto")
        # Vorsorge KV/PV (Basis): Rente + AN-Anteil Lohn (KV um 4 % gekürzt) + Lücken-KV
        kvpv_y = (sm("rente_kv") + sm("rente_pv") + sm("kv_an") * (1 - C.KV_ANTEIL_KRANKENGELD_ABZUG) + sm("pv_an")
                  + sm("luecken_kv")) * s
        rv_an_y = sm("rv_an") * s
        d = {"rente_stpfl": stpfl_rente, "lohn_brutto": wage_y * s, "sonstige": sonst_y * s,
             "kvpv_basis": kvpv_y, "rv_an": rv_an_y, "rv_ag": rv_an_y, "s": s}
        zahlung = zahlungen.get(jahr, 0.0)
        st0 = _jahressteuer(p, ann, jahr, y0, d, 0.0)
        st1 = _jahressteuer(p, ann, jahr, y0, d, zahlung) if zahlung > 0 else st0
        ersparnis = st0["zusatz"] - st1["zusatz"]
        if zahlung > 0 and st1["av_abzug"] - st0["av_abzug"] < zahlung - 1:
            res.warnungen.append(f"{jahr}: Höchstbetrag Altersvorsorge ({C.HOECHSTBETRAG_ALTERSVORSORGE * lf:,.0f} €) begrenzt die Absetzbarkeit – "
                                 f"nur {st1['av_abzug'] - st0['av_abzug']:,.0f} von {zahlung:,.0f} € abziehbar. Verteilung auf mehr Jahre prüfen.")
        steuer_y = st0["zusatz"] / s - ersparnis
        steuer_y = max(0.0, steuer_y)
        # Aufteilung der Steuer auf Einkunftsquellen nach steuerlicher Bemessungsgrundlage
        tb_r = max(0.0, stpfl_rente - st1["wk_rente"]) / s
        tb_w = max(0.0, (wage_y * s - st1["wk_an"])) / s
        tb_s = sonst_y
        tb_sum = tb_r + tb_w + tb_s
        shares = (tb_r / tb_sum, tb_w / tb_sum, tb_s / tb_sum) if tb_sum > 0 else (0, 0, 0)
        gesamt_brutto = {"r": rente_y, "w": wage_y, "s": sonst_y}
        jr = {
            "jahr": jahr, "monate": n_m, "annualisiert": s != 1.0,
            "rente_brutto": rente_y, "rente_stpfl": stpfl_rente / s, "rentenfreibetrag": freibetrag_rente or 0.0,
            "lohn_brutto": wage_y, "sonstige": sonst_y,
            "wk_rente": st1["wk_rente"], "wk_an": st1["wk_an"], "vorsorge_kvpv": kvpv_y / s,
            "altersvorsorge_abzug": st1["av_abzug"] / s, "ausgleichszahlung": zahlung,
            "sa_pauschbetrag": st1["sa_pb"], "zve_person": st1["person_zve"] / s, "partner_zve": st1["partner"],
            "zve_haushalt": st1["haushalt"]["zve"], "est_haushalt": st1["haushalt"]["est"],
            "est_basis_partner": st1["basis"]["est"], "soli": st1["zusatz_soli"], "kist": st1["zusatz_kist"],
            "steuer_person": steuer_y, "steuerersparnis_ausgleich": ersparnis,
            "besteuerungsanteil": ba,
        }
        jahr_rows.append(jr)
        _trace_jahr(tr, jahr, jr, st0, st1, fb_text, ba, s, p)

        # ------------------- Monatsverteilung Netto -----------------------
        steuer_r, steuer_w, steuer_s = (steuer_y * x for x in shares)
        # Ausgleich-Steuerersparnis ist bereits in steuer_y enthalten (senkt Lohnsteuer)
        for r in mrows:
            r["steuer_rente"] = steuer_r * r["rente_brutto"] / rente_y if rente_y > 0 else 0.0
            r["steuer_lohn"] = steuer_w * r["wage"] / wage_y if wage_y > 0 else 0.0
            r["steuer_sonst"] = steuer_s * r["sonstige_brutto"] / sonst_y if sonst_y > 0 else 0.0
            r["rente_netto"] = r["rente_brutto"] - r["rente_kv"] - r["rente_pv"] - r["steuer_rente"]
            gehalt_anteil = (r["gehalt_brutto"] / r["wage"]) if r["wage"] > 0 else 0.0
            wage_netto = (r["wage"] - r["rv_an"] - r["av_an"] - r["kv_an"] - r["pv_an"] - r["steuer_lohn"])
            r["gehalt_netto"] = wage_netto * gehalt_anteil
            hz_pay_netto = wage_netto * (1 - gehalt_anteil)
            r["hinz_netto"] = hz_pay_netto + (r["hinz_brutto"] if r["hinz_minijob"] else 0.0)
            r["sonst_netto"] = r["sonstige_brutto"] - r["steuer_sonst"]
            r["ausgleich_zahlung"] = zahlung if r["monat"] == 12 else 0.0
            r["ausgleich_steuerersparnis"] = ersparnis if r["monat"] == 12 else 0.0
            # ------------- Depot ------------------------------------------
        sparer_rest = C.SPARER_PAUSCHBETRAG * (2 if p.verheiratet else 1)
        for r in mrows:
            i = r["i"]
            depot *= (1 + p.depot_rendite) ** (1 / 12)
            r["depot_zuwachs_basis"] = depot
            netto_ohne_depot = (r["rente_netto"] + r["hinz_netto"] + r["sonst_netto"] - r["luecken_kv"] + r["gehalt_netto"])
            aktiv = sz.wunsch_netto_monat > 0 and i >= ew_idx and (sz.entnahme_modus == "dauerhaft" or i < rb_idx)
            need = max(0.0, sz.wunsch_netto_monat * r["deflator"] - netto_ohne_depot) if aktiv else 0.0
            x = netto_x = tax = luecke = 0.0
            if need > 0 and depot > 0:
                quote = max(0.0, (depot - basis_kosten) / depot) * (1 - p.depot_teilfreistellung)
                x = need
                if x * quote > sparer_rest:
                    x = (need - abg_satz * sparer_rest) / (1 - abg_satz * quote)
                if x > depot:
                    x = depot
                tpfl = x * quote
                tax = abg_satz * max(0.0, tpfl - sparer_rest)
                sparer_rest = max(0.0, sparer_rest - tpfl)
                basis_kosten -= x * basis_kosten / depot if depot > 0 else 0
                depot -= x
                netto_x = x - tax
                luecke = max(0.0, need - netto_x)
            elif need > 0:
                luecke = need
            r.update(entnahme_brutto=x, entnahme_netto=netto_x, entnahme_steuer=tax, depot_luecke=luecke, depot_wert=depot)
        rows.extend(mrows)

    mdf = pd.DataFrame(rows)
    mdf["rente_netto_real"] = mdf["rente_netto"] / mdf["deflator"]
    mdf["netto_verfuegbar"] = (mdf["gehalt_netto"] + mdf["rente_netto"] + mdf["hinz_netto"] + mdf["sonst_netto"]
                               - mdf["luecken_kv"] + mdf["entnahme_netto"])
    mdf["netto_verfuegbar_real"] = mdf["netto_verfuegbar"] / mdf["deflator"]
    mdf["fluss_basis1"] = mdf["rente_netto"] - mdf["ausgleich_zahlung"] + mdf["ausgleich_steuerersparnis"]
    mdf["fluss_basis2"] = mdf["netto_verfuegbar"] - mdf["ausgleich_zahlung"]
    res.monat = mdf
    res.jahr = pd.DataFrame(jahr_rows)
    res.kennzahlen = _kennzahlen(res, p, ann, sz, a1, ep1_bewertet, rb_idx, rag, start_idx, zahlungen, ep_ausgleich)
    if mdf["depot_luecke"].sum() > 1:
        res.warnungen.append(f"Depot reicht für die Überbrückung nicht aus – ungedeckte Lücke insgesamt "
                             f"{mdf['depot_luecke'].sum():,.0f} € nominal.")
    if sz.teilrente_prozent < 100 and sz.hinzuverdienst_monat * 12 > 0:
        res.warnungen.append("Hinzuverdienstgrenze bei Teilrente wird nicht geprüft/angerechnet (seit 2023 keine Grenze bei Vollrente; "
                             "bei Teilrente individuell gem. § 34 SGB VI).")
    return res


# ============================================================================
# Trace für ein Jahr
# ============================================================================
def _trace_jahr(tr: Tracer, jahr: int, jr: dict, st0: dict, st1: dict, fb_text: str, ba: float, s: float, p: Person):
    g = f"Jahr {jahr}"
    if s != 1.0:
        tr.add(g, "Hinweis Annualisierung", f"Teiljahr wird mit Faktor {s:.2f} auf 12 Monate hochgerechnet (Progression) und zurückgerechnet", s)
    if jr["rente_brutto"] > 0:
        tr.add(g, "Rente brutto (Summe der Monate)", "Σ Monatsrente", round(jr["rente_brutto"], 2), "€")
        tr.add(g, "Rentenfreibetrag", fb_text, round(jr["rentenfreibetrag"], 2), "€")
        tr.add(g, "Steuerpflichtiger Rentenanteil", "Jahresrente − Rentenfreibetrag", round(jr["rente_stpfl"], 2), "€")
    if jr["lohn_brutto"] > 0:
        tr.add(g, "Arbeitslohn brutto", "Gehalt + Hinzuverdienst (ohne Minijob)", round(jr["lohn_brutto"], 2), "€")
    if jr["sonstige"] > 0:
        tr.add(g, "Sonstige Einkünfte", "Eingabe, voll steuerpflichtig", round(jr["sonstige"], 2), "€")
    if jr["wk_rente"]:
        tr.add(g, "Werbungskosten-Pauschbetrag Rente", "§ 9a Nr. 3 EStG", -jr["wk_rente"], "€")
    if jr["wk_an"]:
        tr.add(g, "Arbeitnehmer-Pauschbetrag", "§ 9a Nr. 1 EStG", -jr["wk_an"], "€")
    tr.add(g, "Vorsorgeaufwendungen KV/PV (Basis)", "Eigenanteile KV + PV (AN-KV × 96 % im Job)", -round(jr["vorsorge_kvpv"], 2), "€", "§ 10 Abs. 1 Nr. 3 EStG")
    if jr["altersvorsorge_abzug"] > 0 or jr["ausgleichszahlung"] > 0:
        tr.add(g, "Altersvorsorgeaufwendungen", "RV-Beiträge (AN+AG) + Ausgleichszahlung, gedeckelt auf Höchstbetrag, abzgl. AG-Anteil",
               -round(jr["altersvorsorge_abzug"], 2), "€", "§ 10 Abs. 1 Nr. 2, Abs. 3 EStG (100 % abziehbar)")
    tr.add(g, "Sonderausgaben-Pauschbetrag", "§ 10c EStG", -jr["sa_pauschbetrag"], "€")
    tr.add(g, "zvE Person (vor Partner)", "Summe der Posten", round(jr["zve_person"], 2), "€")
    if p.verheiratet:
        tr.add(g, "Partner (zvE-Beitrag)", "Einkünfte − Vorsorge (gewachsen)", round(jr["partner_zve"], 2), "€")
    tr.add(g, "zvE Haushalt", "zvE Person + Partner", round(jr["zve_haushalt"], 2), "€")
    tr.add(g, "Einkommensteuer Haushalt", "Splittingtarif 2·T(zvE/2)" if p.verheiratet else "Grundtarif § 32a EStG",
           round(jr["est_haushalt"], 2), "€")
    if p.verheiratet:
        tr.add(g, "ESt Partner allein (Referenz)", "Inkrementalmethode: Zusatzsteuer = Haushalt − Partner allein",
               round(jr["est_basis_partner"], 2), "€")
    tr.add(g, "Soli + KiSt (zugerechnet)", "Soli: 5,5 % mit Milderungszone", round(jr["soli"] + jr["kist"], 2), "€")
    if jr["steuerersparnis_ausgleich"]:
        tr.add(g, "Steuerersparnis durch Ausgleichszahlung", "Steuer ohne − Steuer mit Sonderzahlung", round(jr["steuerersparnis_ausgleich"], 2), "€")
    tr.add(g, "Steuer der Person gesamt", "ESt + Soli + KiSt (zugerechnet)", round(jr["steuer_person"], 2), "€")


# ============================================================================
# Kennzahlen, Serien, Break-even
# ============================================================================
def serie_monatlich(res: Ergebnis, basis: int, real: bool) -> pd.Series:
    """Monatliches verfügbares Netto (je nach Basis) – Index: Alter in Jahren."""
    m = res.monat
    if basis == 1:
        v = m["rente_netto"]
    else:
        v = m["netto_verfuegbar"]
    if real:
        v = v / m["deflator"]
    return pd.Series(v.values, index=m["alter"].values)


def serie_kumuliert(res: Ergebnis, basis: int, real: bool) -> pd.Series:
    return serie_kumuliert_tmp(res.monat, basis, real, res.kennzahlen["depot_start"])


def _kennzahlen(res, p, ann, sz, a1, ep1_bew, rb_idx, rag, start_idx, zahlungen, ep_ausgleich) -> dict:
    m = res.monat
    k: dict = {"depot_start": p.depot_start}
    first = m[m["i"] == rb_idx]
    k["rentenart"] = a1.art
    k["zugangsfaktor"] = a1.zugangsfaktor
    k["abschlag_prozent"] = a1.abschlag_monate * C.RENTENABSCHLAG_PRO_MONAT * 100
    k["rente_brutto_start"] = float(first["rente_brutto"].iloc[0]) if len(first) else float("nan")
    # Netto-Rente: Durchschnitt im ersten vollen Kalenderjahr (das Rentenbeginn-Jahr enthält ggf. Gehalt/Teiljahr-Effekte)
    jr = rb_idx // 12 if rb_idx % 12 == 0 else rb_idx // 12 + 1
    sel = m[m["jahr"] == jr]
    if len(sel) == 0:
        sel = first
    k["rente_netto_start"] = float(sel["rente_netto"].mean()) if len(sel) else float("nan")
    k["rente_netto_start_real"] = float(sel["rente_netto_real"].mean()) if len(sel) else float("nan")
    k["rente_brutto_1jahr"] = float(sel["rente_brutto"].mean()) if len(sel) else float("nan")
    k["rentenbeginn_alter_m"] = sz.rentenbeginn_alter_m
    k["ausgleich_summe"] = float(sum(zahlungen.values()))
    k["ausgleich_ep"] = ep_ausgleich
    k["steuerersparnis_ausgleich"] = float(m["ausgleich_steuerersparnis"].sum())
    k["depot_ende"] = float(m["depot_wert"].iloc[-1])
    for basis in (1, 2, 3):
        for real in (False, True):
            ser = serie_kumuliert_tmp(m, basis, real, p.depot_start)
            for alter in (75, 80, 85, 90):
                k[f"kum{basis}_{'real' if real else 'nom'}_{alter}"] = _wert_bei_alter(ser, alter)
    for alter in (65, 70, 75, 80, 85, 90):
        row = m[m["alter_m"] == alter * 12 - 1]
        k[f"netto_{alter}_nom"] = float(row["netto_verfuegbar"].iloc[0]) if len(row) else float("nan")
        k[f"netto_{alter}_real"] = float(row["netto_verfuegbar_real"].iloc[0]) if len(row) else float("nan")
        k[f"rente_netto_{alter}_nom"] = float(row["rente_netto"].iloc[0]) if len(row) else float("nan")
        k[f"rente_netto_{alter}_real"] = float(row["rente_netto_real"].iloc[0]) if len(row) else float("nan")
    return k


def serie_kumuliert_tmp(m: pd.DataFrame, basis: int, real: bool, depot_start: float) -> pd.Series:
    defl = m["deflator"] if real else 1.0
    fluss = (m["fluss_basis1"] if basis == 1 else m["fluss_basis2"]) / defl
    cum = fluss.cumsum()
    if basis == 3:
        cum = cum + m["depot_wert"] / defl - depot_start
    return pd.Series(cum.values, index=m["alter"].values)


def _wert_bei_alter(ser: pd.Series, alter: int) -> float:
    """Kumulierter Wert am Ende des Monats, in dem das Alter `alter` erreicht wird."""
    idx = ser.index.values
    pos = np.searchsorted(idx, alter - 1e-9)
    return float(ser.iloc[pos]) if pos < len(ser) else float("nan")


def break_even(a: Ergebnis, b: Ergebnis, basis: int = 1, real: bool = False) -> dict:
    """Break-even zwischen Szenario a (Referenz, früher) und b (später).

    Gesucht: erstes Alter, ab dem die kumulierte Kurve von b die von a dauerhaft (erste
    Überschneidung, lineare Interpolation im Monat) erreicht/übertrifft.
    """
    sa, sb = serie_kumuliert(a, basis, real), serie_kumuliert(b, basis, real)
    n = min(len(sa), len(sb))
    diff = (sb.values[:n] - sa.values[:n])
    alter = sa.index.values[:n]
    out = {"alter": None, "text": "", "status": ""}
    if n < 2:
        out["status"] = "keine Daten"
        return out
    # Referenzzustand: erster signifikanter Unterschied
    nz = np.where(np.abs(diff) > 1.0)[0]
    if len(nz) == 0:
        out.update(status="identisch", text="Verläufe identisch.")
        return out
    start_sign = np.sign(diff[nz[0]])
    if start_sign > 0:
        out.update(status="sofort", alter=float(alter[nz[0]]),
                   text=f"{b.szenario.name} liegt von Beginn an vorn (kein Break-even nötig).")
        return out
    for j in range(nz[0] + 1, n):
        if diff[j] >= 0 > diff[j - 1]:
            t = alter[j - 1] + (alter[j] - alter[j - 1]) * (-diff[j - 1]) / (diff[j] - diff[j - 1])
            out.update(status="ok", alter=float(t),
                       text=f"{b.szenario.name} überholt {a.szenario.name} mit {fmt_alter(t * 12)}.")
            return out
    out.update(status="nie", text=f"Bis Alter {alter[-1]:.0f} holt {b.szenario.name} den Rückstand nicht auf.")
    return out


def break_even_vereinfacht(a: Ergebnis, b: Ergebnis) -> dict:
    """Analytische Näherung (brutto, ohne Dynamik/Steuer):
    n = R_früh · Δ / (R_spät − R_früh) Monate nach dem späteren Rentenbeginn."""
    ka, kb = a.kennzahlen, b.kennzahlen
    ra, rb = ka["rente_netto_start"], kb["rente_netto_start"]
    delta = kb["rentenbeginn_alter_m"] - ka["rentenbeginn_alter_m"]
    if delta <= 0 or rb <= ra:
        return {"alter_m": None, "text": "Nicht definiert (kein späterer Beginn bzw. keine höhere Rente)."}
    n = ra * delta / (rb - ra)
    alter_m = kb["rentenbeginn_alter_m"] + n
    return {"alter_m": alter_m,
            "text": f"n = {ra:,.0f} € × {delta} Mon. / ({rb:,.0f} − {ra:,.0f}) € = {n:.0f} Monate nach späterem Beginn → Alter {fmt_alter(alter_m)}"}


def vergleich_tabelle(ergebnisse: list[Ergebnis], real: bool, basis: int) -> pd.DataFrame:
    rows = []
    suf = "real" if real else "nom"
    for e in ergebnisse:
        if not e.ok:
            rows.append({"Szenario": e.szenario.name, "Rentenart": "nicht möglich"})
            continue
        k = e.kennzahlen
        rows.append({
            "Szenario": e.szenario.name,
            "Rentenart": k["rentenart"],
            "Rentenbeginn": fmt_alter(k["rentenbeginn_alter_m"]),
            "Abschlag %": round(k["abschlag_prozent"], 1),
            "Rente brutto (Start)": round(k["rente_brutto_start"]),
            "Rente netto (1. volles Jahr, Ø/Monat)": round(k[f"rente_netto_start{'_real' if real else ''}"]),
            "Netto verf. Alter 70": round(k[f"netto_70_{suf}"]) if not math.isnan(k[f"netto_70_{suf}"]) else None,
            "Netto verf. Alter 80": round(k[f"netto_80_{suf}"]) if not math.isnan(k[f"netto_80_{suf}"]) else None,
            "Kum. bis 75": _r(k[f"kum{basis}_{suf}_75"]),
            "Kum. bis 80": _r(k[f"kum{basis}_{suf}_80"]),
            "Kum. bis 85": _r(k[f"kum{basis}_{suf}_85"]),
            "Kum. bis 90": _r(k[f"kum{basis}_{suf}_90"]),
            "Ausgleichszahlung": round(k["ausgleich_summe"]),
        })
    return pd.DataFrame(rows)


def _r(x):
    return None if x is None or (isinstance(x, float) and math.isnan(x)) else round(x)
