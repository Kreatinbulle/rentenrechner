"""insights.py – Erklärende Auswertungen für die GUI (Klartext-Fazit, Vorlagen, Einflussanalyse)."""
from __future__ import annotations

import math
from dataclasses import replace

import pandas as pd

import config as C
import engine as E
from models import Annahmen, Person, Szenario


def eur(x, nk=0) -> str:
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return "–"
    return (f"{x:,.{nk}f}").replace(",", "X").replace(".", ",").replace("X", ".") + " €"


# ----------------------------------------------------------------------------
def _idx(d) -> int:
    return d.year * 12 + d.month - 1


def leite_ab(p: Person, ann: Annahmen) -> Person:
    """Rechnet die Eingaben im Stil der Rentenauskunft in die Rechenwerte um (EP, EP/Jahr, Wartezeiten)."""
    rw = p.auskunft_rentenwert or C.RENTENWERT_AB_JULI_2026
    ep = p.anwartschaft_eur / rw if p.ep_modus == "rente" else p.ep_aktuell
    start_idx, birth_idx = _idx(ann.start_datum()), _idx(p.geburt)
    j35, j45 = p.wartezeit_jahre_35, p.wartezeit_jahre_45
    if p.wz_modus == "schaetzung":
        from datetime import date as _d
        bs = _d.fromisoformat(p.berufsstart)
        monate = max(0, start_idx - _idx(bs)) - p.luecken_monate + p.kind_monate
        j45 = max(0.0, monate / 12)
        j35 = max(0.0, (monate + min(p.schul_monate, 96)) / 12)
    if p.fortgang_modus == "gehalt":
        epj = min(p.brutto_jahr, C.BBG_RV_JAHR) / C.DURCHSCHNITTSENTGELT_JAHR
    elif p.fortgang_modus == "hochrechnung" and p.hochrechnung_eur > 0:
        mon = birth_idx + p.hochrechnung_alter_m + 1 - start_idx
        if mon <= 0:
            epj = 0.0
        else:
            an = E.rentenanspruch(p.geburt.year, p.hochrechnung_alter_m, j35 + mon / 12, j45 + mon / 12)
            zf = an.zugangsfaktor if an.ok else 1.0
            epj = max(0.0, (p.hochrechnung_eur / (rw * zf) - ep) / (mon / 12))
    else:
        epj = p.ep_pro_jahr
    return replace(p, ep_aktuell=ep, ep_pro_jahr=epj, wartezeit_jahre_35=j35, wartezeit_jahre_45=j45)


def fahrplan(p: Person, ann: Annahmen) -> dict:
    """Früheste Rentenbeginne (Alter in Monaten + Datum) – abhängig von den Versicherungszeiten.
    `p` muss bereits ``leite_ab`` durchlaufen haben."""
    gj = p.geburt.year
    rag = C.regelaltersgrenze_monate(gj)
    bl = C.abschlagsfrei_besonders_langjaehrig_monate(gj)
    start_idx, birth_idx = _idx(ann.start_datum()), _idx(p.geburt)
    lo = max(start_idx - birth_idx, 0)

    def wz(m):
        am = max(0, birth_idx + m + 1 - start_idx)
        return p.wartezeit_jahre_35 + am / 12, p.wartezeit_jahre_45 + am / 12

    lang = next((m for m in range(max(C.FRUEHESTENS_LANGJAEHRIG_MONATE, lo), rag + 1)
                 if E.rentenanspruch(gj, m, *wz(m)).ok), rag)
    abs_ = next((m for m in range(max(bl, lo), rag + 1)
                 if (a := E.rentenanspruch(gj, m, *wz(m))).ok and a.zugangsfaktor >= 1.0), rag)

    def datum(m):
        i = birth_idx + m + 1
        return f"{i % 12 + 1:02d}/{i // 12}"

    return {"rag": rag, "langjaehrig": lang, "abschlagsfrei": abs_, "datum": datum}


def vorlagen(p: Person, ann: Annahmen) -> dict[str, Szenario]:
    f = fahrplan(p, ann)
    rag, frueh, frei = f["rag"], f["langjaehrig"], f["abschlagsfrei"]
    return {
        "So früh wie möglich (mit Abschlag)": Szenario(
            name="So früh wie möglich", erwerbsende_alter_m=frueh, rentenbeginn_alter_m=frueh),
        "Abschlagsfrei nach 45 Beitragsjahren": Szenario(
            name="Abschlagsfrei (45 Jahre)", erwerbsende_alter_m=frei, rentenbeginn_alter_m=frei),
        "Zur Regelaltersgrenze": Szenario(
            name="Regelaltersgrenze", erwerbsende_alter_m=rag, rentenbeginn_alter_m=rag),
        "Früh in Rente + Abschlag ausgleichen (§ 187a)": Szenario(
            name="Früh + Ausgleichszahlung", erwerbsende_alter_m=frueh, rentenbeginn_alter_m=frueh,
            ausgleich_modus="voll", ausgleich_jahre=3),
        "Früher aufhören, Rente später (Brücke aus Ersparnissen)": Szenario(
            name="Brücke aus Ersparnissen", erwerbsende_alter_m=max(frueh - 12, 60 * 12), rentenbeginn_alter_m=rag,
            wunsch_netto_monat=2500.0, entnahme_modus="bis_rente"),
        "Teilrente + Minijob": Szenario(
            name="Teilrente + Minijob", erwerbsende_alter_m=frueh, rentenbeginn_alter_m=frueh, teilrente_prozent=50.0,
            vollrente_alter_m=rag, hinzuverdienst_monat=550.0, hinzuverdienst_bis_alter_m=rag),
    }


def beschreibe(sz: Szenario, e: E.Ergebnis | None = None) -> str:
    """Szenario in einem Satz."""
    s = f"Du arbeitest bis {E.fmt_alter(sz.erwerbsende_alter_m)} und beziehst ab {E.fmt_alter(sz.rentenbeginn_alter_m)} Rente"
    if 0 < sz.teilrente_prozent < 100:
        s += f" (zunächst {sz.teilrente_prozent:.0f} % als Teilrente"
        s += f", ab {E.fmt_alter(sz.vollrente_alter_m)} voll)" if sz.vollrente_alter_m > sz.rentenbeginn_alter_m else ")"
    s += "."
    if sz.ausgleich_modus != "keine":
        s += " Den Rentenabschlag gleichst du per Sonderzahlung aus" + (" (voll)." if sz.ausgleich_modus == "voll" else ".")
    if sz.hinzuverdienst_monat > 0:
        s += f" Daneben verdienst du ca. {eur(sz.hinzuverdienst_monat)}/Monat (heutige Kaufkraft) hinzu."
    if sz.wunsch_netto_monat > 0:
        s += (f" Fehlendes Einkommen bis {eur(sz.wunsch_netto_monat)}/Monat netto holst du aus Ersparnissen"
              + (" bis zum Rentenbeginn." if sz.entnahme_modus == "bis_rente" else "."))
    if e is not None and e.ok:
        k = e.kennzahlen
        s += f" → **{k['rentenart']}**"
        if k["abschlag_prozent"] > 0:
            s += f", Abschlag **{k['abschlag_prozent']:.1f} %** (dauerhaft)"
        s += f", Start-Rente ca. **{eur(k['rente_brutto_start'])} brutto**."
    return s


# ----------------------------------------------------------------------------
def _kum(e: E.Ergebnis, basis: int, real: bool, alter: int) -> float:
    return E.serie_kumuliert(e, basis, real).pipe(lambda s: E._wert_bei_alter(s, alter))


def fuehrung_tabelle(ergs: list[E.Ergebnis], basis: int, real: bool, alter_liste=(75, 80, 85, 90)) -> pd.DataFrame:
    rows = []
    for a in alter_liste:
        vals = {e.szenario.name: _kum(e, basis, real, a) for e in ergs}
        vals = {k: v for k, v in vals.items() if not math.isnan(v)}
        if not vals:
            continue
        best = max(vals, key=vals.get)
        rest = sorted(vals.values(), reverse=True)
        rows.append({"Alter": a, "Vorn liegt": best, "Vorsprung": (rest[0] - rest[1]) if len(rest) > 1 else 0.0})
    return pd.DataFrame(rows)


def fazit(ergs: list[E.Ergebnis], basis: int, real: bool, alter: int) -> list[str]:
    if len(ergs) < 2:
        return ["Lege mindestens zwei Szenarien an, um sie zu vergleichen."]
    vals = {e.szenario.name: _kum(e, basis, real, alter) for e in ergs}
    ordered = sorted(vals.items(), key=lambda kv: -kv[1])
    (b1, v1), (b2, v2) = ordered[0], ordered[1]
    worst, vw = ordered[-1]
    out = [f"Bis zum Alter **{alter}** liegt **{b1}** vorn – {eur(v1 - v2)} vor „{b2}“ und {eur(v1 - vw)} vor „{worst}“."]
    lt = fuehrung_tabelle(ergs, basis, real)
    if len(lt) > 1 and lt["Vorn liegt"].nunique() > 1:
        out.append("Die Reihenfolge hängt stark von der Lebensdauer ab: " +
                   "; ".join(f"mit {r['Alter']} → {r['Vorn liegt']}" for _, r in lt.iterrows()) + ".")
    elif len(lt) > 1:
        out.append(f"„{lt['Vorn liegt'].iloc[0]}“ führt in allen betrachteten Altern (75–90).")
    return out


# ----------------------------------------------------------------------------
def einfluss(p: Person, ann: Annahmen, szs: list[Szenario], idx: int, ref: int, basis: int, real: bool, alter: int) -> pd.DataFrame:
    """Wie stark ändert sich der Vorsprung von Szenario idx gegenüber ref (Kumulierung bei `alter`),
    wenn eine Annahme verändert wird?"""

    def metrik(pp: Person, aa: Annahmen) -> float:
        a, b = E.simuliere(pp, aa, szs[idx]), E.simuliere(pp, aa, szs[ref])
        if not (a.ok and b.ok):
            return float("nan")
        return _kum(a, basis, real, alter) - _kum(b, basis, real, alter)

    basis_wert = metrik(p, ann)
    faktoren = [
        ("Rentenanpassung p.a.", f"{ann.rentensteigerung - .01:.1%}", f"{ann.rentensteigerung + .01:.1%}",
         p, replace(ann, rentensteigerung=ann.rentensteigerung - .01), p, replace(ann, rentensteigerung=ann.rentensteigerung + .01)),
        ("Inflation p.a.", f"{ann.inflation - .01:.1%}", f"{ann.inflation + .01:.1%}",
         p, replace(ann, inflation=ann.inflation - .01), p, replace(ann, inflation=ann.inflation + .01)),
        ("Gehaltssteigerung p.a.", f"{ann.lohnsteigerung - .01:.1%}", f"{ann.lohnsteigerung + .01:.1%}",
         p, replace(ann, lohnsteigerung=ann.lohnsteigerung - .01), p, replace(ann, lohnsteigerung=ann.lohnsteigerung + .01)),
        ("Entgeltpunkte je Arbeitsjahr", f"{p.ep_pro_jahr * .8:.2f}", f"{p.ep_pro_jahr * 1.2:.2f}",
         replace(p, ep_pro_jahr=p.ep_pro_jahr * .8), ann, replace(p, ep_pro_jahr=p.ep_pro_jahr * 1.2), ann),
        ("Steuertarif-Indexierung", "0 % (kalte Progression)", "4 %",
         p, replace(ann, tarif_indexierung=0.0), p, replace(ann, tarif_indexierung=0.04)),
        ("KV-Zusatzbeitrag", f"{max(ann.zusatzbeitrag - .01, 0):.1%}", f"{ann.zusatzbeitrag + .01:.1%}",
         p, replace(ann, zusatzbeitrag=max(ann.zusatzbeitrag - .01, 0)), p, replace(ann, zusatzbeitrag=ann.zusatzbeitrag + .01)),
    ]
    if p.depot_start > 0:
        faktoren.append(("Depot-Rendite p.a.", f"{p.depot_rendite - .02:.1%}", f"{p.depot_rendite + .02:.1%}",
                         replace(p, depot_rendite=p.depot_rendite - .02), ann, replace(p, depot_rendite=p.depot_rendite + .02), ann))
    aktuell = {"Rentenanpassung p.a.": f"{ann.rentensteigerung:.1%}", "Inflation p.a.": f"{ann.inflation:.1%}",
               "Gehaltssteigerung p.a.": f"{ann.lohnsteigerung:.1%}", "Entgeltpunkte je Arbeitsjahr": f"{p.ep_pro_jahr:.2f}",
               "Steuertarif-Indexierung": f"{ann.tarif_indexierung:.1%}", "KV-Zusatzbeitrag": f"{ann.zusatzbeitrag:.1%}",
               "Depot-Rendite p.a.": f"{p.depot_rendite:.1%}"}
    rows = []
    for name, ll, lh, p1, a1, p2, a2 in faktoren:
        rows.append({"Faktor": name, "aktuell": aktuell[name], "niedrig": ll, "hoch": lh,
                     "Δ niedrig": metrik(p1, a1) - basis_wert, "Δ hoch": metrik(p2, a2) - basis_wert})
    df = pd.DataFrame(rows)
    df["Spanne"] = (df["Δ niedrig"].abs()).combine(df["Δ hoch"].abs(), max)
    df.attrs["basis_wert"] = basis_wert
    return df.sort_values("Spanne", ascending=True)


# ----------------------------------------------------------------------------
def zerlegung(a: E.Ergebnis, ref: E.Ergebnis, basis: int, real: bool, alter: int) -> pd.DataFrame:
    """Zerlegt den Vorsprung von `a` gegenüber `ref` (kumuliert bis `alter`) in seine Bestandteile.
    Die Summe der Zeilen entspricht exakt der Differenz der Kumulierungskurven."""

    def summen(e: E.Ergebnis) -> dict:
        m = e.monat
        idx = m["alter"].values
        pos = int(pd.Index(idx).searchsorted(alter - 1e-9))
        pos = min(pos, len(m) - 1)
        mm = m.iloc[: pos + 1]
        d = mm["deflator"] if real else 1.0
        sm = lambda col: float((mm[col] / d).sum())  # noqa: E731
        out = {
            "Bruttorente": sm("rente_brutto"),
            "Kranken- & Pflegeversicherung": -(sm("rente_kv") + sm("rente_pv")),
            "Steuer auf die Rente": -sm("steuer_rente"),
            "Ausgleichszahlung (§ 187a)": -sm("ausgleich_zahlung"),
            "Steuerersparnis Ausgleichszahlung": sm("ausgleich_steuerersparnis") if basis == 1 else 0.0,
        }
        if basis >= 2:
            out.update({
                "Gehalt (netto)": sm("gehalt_netto"),
                "Nebenjob (netto)": sm("hinz_netto"),
                "Sonstige Einkünfte (netto)": sm("sonst_netto"),
                "KV in der Erwerbslücke": -sm("luecken_kv"),
                "Depot-Entnahmen (netto)": sm("entnahme_netto"),
            })
        if basis == 3:
            v = float((mm["depot_wert"].iloc[-1] / (mm["deflator"].iloc[-1] if real else 1.0)) - e.kennzahlen["depot_start"])
            out["Depot-Wertveränderung"] = v
        return out

    sa, sr = summen(a), summen(ref)
    rows = [{"Komponente": k, "Δ": sa[k] - sr[k]} for k in sa]
    df = pd.DataFrame(rows)
    return df[df["Δ"].abs() >= 1].reset_index(drop=True)


# ----------------------------------------------------------------------------
def _alt(m: int) -> str:
    return f"{m // 12}" if m % 12 == 0 else f"{m // 12} J. {m % 12} M."


def auto_name(sz: Szenario, p: Person, ann: Annahmen) -> str:
    """Sprechender Name aus den Einstellungen, z. B. „Rente mit 63 (Abschlag 14,4 %)“."""
    rb = sz.rentenbeginn_alter_m
    ew = min(sz.erwerbsende_alter_m, rb)
    st = ann.start_datum()
    birth_idx, start_idx = p.geburt.year * 12 + p.geburt.month - 1, st.year * 12 + st.month - 1
    arbeitsm = max(0, birth_idx + ew + 1 - start_idx)
    an = E.rentenanspruch(p.geburt.year, rb, p.wartezeit_jahre_35 + arbeitsm / 12, p.wartezeit_jahre_45 + arbeitsm / 12)
    rag = C.regelaltersgrenze_monate(p.geburt.year)
    if 0 < sz.teilrente_prozent < 100 and sz.vollrente_alter_m > rb:
        base = f"Teilrente {sz.teilrente_prozent:.0f} % ab {_alt(rb)}, voll ab {_alt(sz.vollrente_alter_m)}"
    elif rb - ew >= 6:
        base = f"Aufhören mit {_alt(ew)}, Rente mit {_alt(rb)}"
    else:
        base = f"Rente mit {_alt(rb)}"
    if sz.ausgleich_modus != "keine":
        base += " + Ausgleichszahlung"
    if sz.hinzuverdienst_monat > 0:
        base += " + Nebenjob"
    if not an.ok:
        tag = "nicht möglich"
    elif an.abschlag_monate:
        tag = f"Abschlag {an.abschlag_monate * C.RENTENABSCHLAG_PRO_MONAT * 100:.1f} %".replace(".", ",")
    elif an.zuschlag_monate:
        tag = f"Zuschlag {an.zuschlag_monate * C.RENTENZUSCHLAG_PRO_MONAT * 100:.1f} %".replace(".", ",")
    else:
        tag = "regulär" if rb >= rag else "abschlagsfrei"
    return f"{base} ({tag})"
