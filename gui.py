"""
gui.py – Streamlit-Dashboard (UI). Start:  streamlit run gui.py
"""
from __future__ import annotations

import json
import math
from dataclasses import asdict
from datetime import date

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import config as C
import engine as E
from models import Annahmen, Person, Projekt, Szenario, standard_projekt

FARBEN = ["#0072B2", "#D55E00", "#009E73", "#CC79A7", "#E69F00", "#56B4E9", "#7A7A7A", "#B2182B"]


def eur(x, nachkomma=0) -> str:
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return "–"
    s = f"{x:,.{nachkomma}f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return s + " €"


# ============================================================================
# Session-State-Verwaltung
# ============================================================================
PERSON_KEYS = list(asdict(Person()).keys())
ANN_KEYS = list(asdict(Annahmen()).keys())


def _lade_projekt(pr: Projekt):
    """Schreibt ein Projekt in den Session-State (vor Widget-Erzeugung aufrufen)."""
    for k, v in asdict(pr.person).items():
        if k == "kinder_geburtsjahre":
            v = ", ".join(str(x) for x in v)
        st.session_state[f"p_{k}"] = v
    st.session_state["p_geburtsdatum"] = pr.person.geburt
    for k, v in asdict(pr.annahmen).items():
        st.session_state[f"a_{k}"] = v
    st.session_state["a_start"] = pr.annahmen.start_datum()
    # alte Szenario-Keys entfernen
    for k in [k for k in st.session_state if k.startswith("s") and k[1:2].isdigit()]:
        del st.session_state[k]
    st.session_state["sz_ids"] = []
    st.session_state["sz_next"] = 0
    for s in pr.szenarien:
        _neues_szenario(s)


def _neues_szenario(s: Szenario):
    sid = st.session_state["sz_next"]
    st.session_state["sz_next"] += 1
    st.session_state["sz_ids"].append(sid)
    d = asdict(s)
    for k, v in d.items():
        if k.endswith("_alter_m"):
            base = k[:-8]
            st.session_state[f"s{sid}_{base}_j"] = v // 12
            st.session_state[f"s{sid}_{base}_m"] = v % 12
        else:
            st.session_state[f"s{sid}_{k}"] = v


def _person_aus_state() -> Person:
    d = {k: st.session_state[f"p_{k}"] for k in PERSON_KEYS}
    d["geburtsdatum"] = d["geburtsdatum"].isoformat()
    kg = str(d["kinder_geburtsjahre"]).replace(";", ",")
    d["kinder_geburtsjahre"] = [int(x) for x in kg.split(",") if x.strip().isdigit()]
    return Person.from_dict(d)


def _annahmen_aus_state() -> Annahmen:
    d = {k: st.session_state[f"a_{k}"] for k in ANN_KEYS}
    s = d["start"]
    d["start"] = f"{s.year}-{s.month:02d}"
    return Annahmen.from_dict(d)


def _szenario_aus_state(sid: int) -> Szenario:
    d = {}
    for f in asdict(Szenario()).keys():
        if f.endswith("_alter_m"):
            base = f[:-8]
            d[f] = int(st.session_state[f"s{sid}_{base}_j"]) * 12 + int(st.session_state[f"s{sid}_{base}_m"])
        else:
            d[f] = st.session_state[f"s{sid}_{f}"]
    return Szenario.from_dict(d)


def _projekt_aus_state() -> Projekt:
    return Projekt(_person_aus_state(), _annahmen_aus_state(),
                   [_szenario_aus_state(i) for i in st.session_state["sz_ids"]])


@st.cache_data(show_spinner=False)
def _rechne(person_json: str, ann_json: str, sz_json: str) -> E.Ergebnis:
    return E.simuliere(Person.from_dict(json.loads(person_json)), Annahmen.from_dict(json.loads(ann_json)),
                       Szenario.from_dict(json.loads(sz_json)))


# ============================================================================
# Eingabe-Widgets
# ============================================================================
def eingaben_person():
    st.subheader("Person")
    c1, c2 = st.columns(2)
    with c1:
        st.date_input("Geburtsdatum", key="p_geburtsdatum", min_value=date(1940, 1, 1), max_value=date(2010, 12, 31),
                      help="Bestimmt Regelaltersgrenze, abschlagsfreies Alter und Steuerkohorte.")
        st.number_input("Entgeltpunkte aktuell (Rentenauskunft)", key="p_ep_aktuell", min_value=0.0, step=0.5, format="%.3f")
        st.number_input("Erwartete EP je weiterem Erwerbsjahr", key="p_ep_pro_jahr", min_value=0.0, step=0.1, format="%.3f",
                        help="Faustformel: Jahresbrutto / Durchschnittsentgelt "
                             f"({C.DURCHSCHNITTSENTGELT_JAHR:,.0f} €), max. BBG {C.BBG_RV_JAHR:,.0f} €.")
        pe = min(st.session_state["p_brutto_jahr"], C.BBG_RV_JAHR) / C.DURCHSCHNITTSENTGELT_JAHR
        st.caption(f"Vorschlag aus Jahresbrutto: **{pe:.2f} EP/Jahr**")
        st.number_input("Wartezeit-Jahre für 35-Jahre-Wartezeit (heute)", key="p_wartezeit_jahre_35", min_value=0.0, step=0.5)
        st.number_input("Wartezeit-Jahre für 45-Jahre-Wartezeit (heute)", key="p_wartezeit_jahre_45", min_value=0.0, step=0.5,
                        help="Pflichtbeiträge, Berücksichtigungszeiten, ALG-I-Zeiten (die letzten 2 Jahre vor Rentenbeginn zählen nur eingeschränkt).")
    with c2:
        st.number_input("Aktuelles Bruttojahresgehalt (€)", key="p_brutto_jahr", min_value=0.0, step=1000.0)
        st.selectbox("Steuerklasse", list(C.STEUERKLASSEN.keys()), key="p_steuerklasse",
                     format_func=lambda k: C.STEUERKLASSEN[k],
                     help="Klasse 3/4/4F/5 → Zusammenveranlagung (Splitting). Die Klasse steuert nur den monatlichen "
                          "Lohnsteuerabzug; die endgültige Steuerlast folgt aus der Veranlagung.")
        verh = st.session_state["p_steuerklasse"] in C.SPLITTING_KLASSEN
        st.number_input("Partner: Einkünfte p.a. (nach Werbungskosten/Rentenfreibetrag)", key="p_partner_einkuenfte_jahr",
                        min_value=0.0, step=1000.0, disabled=not verh)
        st.number_input("Partner: abziehbare Vorsorgeaufwendungen p.a.", key="p_partner_vorsorge_jahr", min_value=0.0,
                        step=500.0, disabled=not verh)
        st.number_input("Partner: Einkommenssteigerung p.a.", key="p_partner_wachstum", step=0.005, format="%.3f", disabled=not verh)
        st.text_input("Geburtsjahre der Kinder (kommagetrennt)", key="p_kinder_geburtsjahre",
                      help="Steuert PV-Kinderlosenzuschlag und Abschlag für Kinder unter 25.")
        st.selectbox("Kirchensteuer", [0.0, 0.08, 0.09], key="p_kirchensteuer", format_func=lambda x: f"{x:.0%}")
    st.subheader("Kranken-/Pflegeversicherung im Ruhestand")
    c1, c2, c3 = st.columns(3)
    with c1:
        st.selectbox("KV-Modus", ["auto", "kvdr", "freiwillig"], key="p_kv_modus",
                     format_func={"auto": "automatisch (9/10-Prüfung)", "kvdr": "KVdR (Pflichtversicherung)",
                                  "freiwillig": "freiwillige GKV"}.get)
    with c2:
        st.number_input("GKV-Anteil in 2. Erwerbshälfte (%)", key="p_gkv_anteil_zweite_haelfte", min_value=0.0, max_value=100.0,
                        step=1.0, help="9/10-Regel: mind. 90 % der zweiten Hälfte des Erwerbslebens Pflicht-/Familienversichert.")
        modus, grund = E.kv_modus_effektiv(_person_aus_state())
        st.caption(("✅ " if modus == "kvdr" else "⚠️ ") + grund)
    with c3:
        st.checkbox("In Erwerbslücke familienversichert (keine KV-Kosten)", key="p_luecken_kv_familienversichert")
        st.number_input("Sonstige Einkünfte p.a. (Miete, Betriebsrente; voll steuerpflichtig)", key="p_sonstige_einkuenfte_jahr",
                        min_value=0.0, step=500.0)
    st.subheader("Privatvermögen / Depot")
    c1, c2, c3, c4 = st.columns(4)
    c1.number_input("Depotwert heute (€)", key="p_depot_start", min_value=0.0, step=5000.0)
    c2.number_input("Gewinnanteil im Depot", key="p_depot_gewinnanteil", min_value=0.0, max_value=1.0, step=0.05, format="%.2f")
    c3.number_input("Rendite p.a. (nominal)", key="p_depot_rendite", step=0.005, format="%.3f")
    c4.number_input("Teilfreistellung", key="p_depot_teilfreistellung", min_value=0.0, max_value=1.0, step=0.05, format="%.2f",
                    help="30 % bei Aktienfonds. Abgeltungsteuer 25 % + Soli; Sparer-Pauschbetrag 1.000/2.000 €.")


def eingaben_annahmen():
    st.subheader("Annahmen")
    c1, c2, c3 = st.columns(3)
    c1.date_input("Simulationsstart (Monat)", key="a_start", help="Heute; Gehalt wird ab hier bis Erwerbsende fortgeschrieben.")
    c1.number_input("Horizont (Alter)", key="a_horizont_alter", min_value=80, max_value=105, step=1)
    c2.number_input("Rentenanpassung p.a. (1. Juli)", key="a_rentensteigerung", step=0.005, format="%.3f")
    c2.number_input("Inflation p.a.", key="a_inflation", step=0.005, format="%.3f")
    c3.number_input("Lohnsteigerung p.a. (Gehalt, BBG, Ø-Entgelt)", key="a_lohnsteigerung", step=0.005, format="%.3f")
    c3.number_input("Indexierung Steuertarif p.a.", key="a_tarif_indexierung", step=0.005, format="%.3f",
                    help="0 % = kalte Progression; Standard: Anpassung an Inflation.")
    c3.number_input("KV-Zusatzbeitrag", key="a_zusatzbeitrag", step=0.001, format="%.3f")


def editor_szenario(sid: int, idx: int):
    name = st.session_state[f"s{sid}_name"]
    with st.expander(f"{idx + 1}. {name}", expanded=False):
        st.text_input("Name", key=f"s{sid}_name")
        c = st.columns(4)
        c[0].markdown("**Erwerbsende**")
        c[0].number_input("Jahre", key=f"s{sid}_erwerbsende_j", min_value=50, max_value=75, step=1)
        c[0].number_input("Monate", key=f"s{sid}_erwerbsende_m", min_value=0, max_value=11, step=1)
        c[1].markdown("**Rentenbeginn**")
        c[1].number_input("Jahre ", key=f"s{sid}_rentenbeginn_j", min_value=60, max_value=75, step=1)
        c[1].number_input("Monate ", key=f"s{sid}_rentenbeginn_m", min_value=0, max_value=11, step=1)
        c[2].markdown("**Teilrente**")
        c[2].number_input("Anteil % (100 = Vollrente)", key=f"s{sid}_teilrente_prozent", min_value=10.0, max_value=100.0, step=10.0)
        c[2].number_input("Wechsel auf Vollrente mit Jahren (0 = nie)", key=f"s{sid}_vollrente_j", min_value=0, max_value=75, step=1)
        c[2].number_input("… Monate", key=f"s{sid}_vollrente_m", min_value=0, max_value=11, step=1)
        c[3].markdown("**Hinzuverdienst**")
        c[3].number_input("brutto/Monat (heutige Kaufkraft)", key=f"s{sid}_hinzuverdienst_monat", min_value=0.0, step=100.0,
                          help=f"Bis {C.MINIJOB_GRENZE_MONAT:.0f} € Minijob (steuer-/abgabenfrei für AN), darüber normal sozialversicherungspflichtig.")
        c[3].number_input("bis Alter (Jahre)", key=f"s{sid}_hinzuverdienst_bis_j", min_value=55, max_value=80, step=1)
        c[3].number_input("bis Alter (Monate)", key=f"s{sid}_hinzuverdienst_bis_m", min_value=0, max_value=11, step=1)
        c[3].checkbox("RV-Beiträge entrichten (EP-Zuschläge)", key=f"s{sid}_rv_aufstocken")
        c = st.columns(4)
        c[0].selectbox("Ausgleichszahlung § 187a", ["keine", "voll", "betrag"], key=f"s{sid}_ausgleich_modus",
                       format_func={"keine": "keine", "voll": "voller Ausgleich", "betrag": "fester Betrag"}.get)
        c[1].number_input("Betrag gesamt (€)", key=f"s{sid}_ausgleich_betrag", min_value=0.0, step=1000.0,
                          disabled=st.session_state[f"s{sid}_ausgleich_modus"] != "betrag")
        c[2].number_input("Jahresraten (vor Rentenbeginn)", key=f"s{sid}_ausgleich_jahre", min_value=1, max_value=10, step=1)
        c[3].number_input("Wunsch-Netto/Monat (heutige Kaufkraft)", key=f"s{sid}_wunsch_netto_monat", min_value=0.0, step=100.0,
                          help="Fehlbetrag zwischen Wunsch-Netto und übrigem Netto wird aus dem Depot entnommen (0 = keine Entnahme).")
        c[3].selectbox("Entnahme", ["bis_rente", "dauerhaft"], key=f"s{sid}_entnahme_modus",
                       format_func={"bis_rente": "nur bis Rentenbeginn", "dauerhaft": "dauerhaft"}.get)
        b1, b2, _ = st.columns([1, 1, 4])
        if b1.button("Duplizieren", key=f"s{sid}_dup"):
            s = _szenario_aus_state(sid)
            s.name += " (Kopie)"
            _neues_szenario(s)
            st.rerun()
        if b2.button("Löschen", key=f"s{sid}_del"):
            st.session_state["sz_ids"].remove(sid)
            st.rerun()


# ============================================================================
# Diagramme
# ============================================================================
def _layout(fig: go.Figure, titel: str, ytitel: str) -> go.Figure:
    fig.update_layout(title=titel, hovermode="x unified", height=430, margin=dict(l=10, r=10, t=50, b=10),
                      xaxis_title="Lebensalter", yaxis_title=ytitel, legend=dict(orientation="h", y=-0.2),
                      yaxis_tickformat=",.0f")
    return fig


def chart_monatlich(ergs, basis, real):
    fig = go.Figure()
    for n, e in enumerate(ergs):
        s = E.serie_monatlich(e, basis, real)
        fig.add_trace(go.Scatter(x=s.index, y=s.values, name=e.szenario.name, mode="lines",
                                 line=dict(color=FARBEN[n % len(FARBEN)], width=2.5),
                                 hovertemplate="%{y:,.0f} €"))
    return _layout(fig, "Monatliche Netto-Kaufkraft" if real else "Monatliches Netto (nominal)", "€ / Monat")


def chart_kumuliert(ergs, basis, real, ref_idx):
    fig = go.Figure()
    for n, e in enumerate(ergs):
        s = E.serie_kumuliert(e, basis, real)
        fig.add_trace(go.Scatter(x=s.index, y=s.values, name=e.szenario.name, mode="lines",
                                 line=dict(color=FARBEN[n % len(FARBEN)], width=2.5), hovertemplate="%{y:,.0f} €"))
    ref = ergs[ref_idx]
    for n, e in enumerate(ergs):
        if n == ref_idx:
            continue
        be = E.break_even(ref, e, basis, real)
        if be["status"] == "ok":
            y = float(E.serie_kumuliert(e, basis, real).iloc[(E.serie_kumuliert(e, basis, real).index >= be["alter"]).argmax()])
            fig.add_trace(go.Scatter(x=[be["alter"]], y=[y], mode="markers", showlegend=False,
                                     marker=dict(size=11, symbol="diamond", color=FARBEN[n % len(FARBEN)], line=dict(width=2, color="white")),
                                     hovertemplate=f"Break-even {e.szenario.name}: {be['alter']:.1f} J.<extra></extra>"))
    return _layout(fig, "Kumulierte Netto-Werte (Schnittpunkte = Break-even)", "€ kumuliert seit Start")


def chart_differenz(ergs, basis, real, ref_idx):
    fig = go.Figure()
    ref = E.serie_kumuliert(ergs[ref_idx], basis, real)
    for n, e in enumerate(ergs):
        if n == ref_idx:
            continue
        s = E.serie_kumuliert(e, basis, real)
        k = min(len(s), len(ref))
        fig.add_trace(go.Scatter(x=s.index[:k], y=s.values[:k] - ref.values[:k], name=e.szenario.name,
                                 line=dict(color=FARBEN[n % len(FARBEN)], width=2.5), hovertemplate="%{y:,.0f} €"))
    fig.add_hline(y=0, line_dash="dot", line_color="#888")
    return _layout(fig, f"Differenz zu „{ergs[ref_idx].szenario.name}“ (Nulldurchgang = Break-even)", "€ Differenz")


def chart_depot(ergs, real):
    fig = go.Figure()
    for n, e in enumerate(ergs):
        m = e.monat
        y = m["depot_wert"] / (m["deflator"] if real else 1)
        fig.add_trace(go.Scatter(x=m["alter"], y=y, name=e.szenario.name, line=dict(color=FARBEN[n % len(FARBEN)], width=2.5),
                                 hovertemplate="%{y:,.0f} €"))
    return _layout(fig, "Depotverlauf (Überbrückung / Kapitalverzehr)", "€")


# ============================================================================
# Hauptprogramm
# ============================================================================
def main():
    st.set_page_config(page_title="Renten- & Früheintritts-Szenariorechner", layout="wide", page_icon="📈")
    if "sz_ids" not in st.session_state:
        _lade_projekt(standard_projekt())

    st.title("Renten- & Früheintritts-Szenariorechner")
    st.caption("Entscheidungshilfe für Rentenbeginn, Abschläge, Ausgleichszahlung, Teilrente und Überbrückung – "
               "keine Rechts-, Steuer- oder Anlageberatung. Alle Werte sind Modellrechnungen.")

    with st.sidebar:
        st.header("Projekt")
        pr_now = _projekt_aus_state()
        st.download_button("💾 Szenarien speichern (JSON)", pr_now.to_json(), file_name="rentenprojekt.json", mime="application/json")
        up = st.file_uploader("📂 Projekt laden", type="json")
        if up is not None and st.session_state.get("_geladen") != up.file_id:
            st.session_state["_geladen"] = up.file_id
            try:
                _lade_projekt(Projekt.from_json(up.getvalue().decode("utf-8")))
                st.rerun()
            except Exception as ex:  # noqa: BLE001
                st.error(f"Datei konnte nicht gelesen werden: {ex}")
        if st.button("Zurücksetzen (Beispiel)"):
            _lade_projekt(standard_projekt())
            st.rerun()
        st.divider()
        st.header("Anzeige")
        real = st.radio("Werte", ["Nominal", "Real (Kaufkraft heute)"], horizontal=False) .startswith("Real")
        basis = st.radio("Vergleichsbasis (Kumulierung / Break-even)", [1, 2, 3], format_func=lambda b: E.BASIS_NAMEN[b], index=0)
        st.caption("Basis 1 = klassischer Renten-Break-even. Basis 2/3 berücksichtigen zusätzlich Gehalt in den "
                   "Mehrarbeitsjahren, Hinzuverdienst und Depot.")

    t_dash, t_in, t_sz, t_trace, t_recht, t_info = st.tabs(
        ["📊 Dashboard", "👤 Person & Annahmen", "🧩 Szenarien", "🔍 Rechenweg", "⚖️ Gesetzliche Parameter", "ℹ️ Methodik & Grenzen"])

    with t_in:
        eingaben_person()
        eingaben_annahmen()

    with t_sz:
        st.subheader("Szenarien")
        for idx, sid in enumerate(list(st.session_state["sz_ids"])):
            editor_szenario(sid, idx)
        if st.button("➕ Szenario hinzufügen"):
            _neues_szenario(Szenario(name=f"Szenario {len(st.session_state['sz_ids']) + 1}"))
            st.rerun()

    proj = _projekt_aus_state()
    pj, aj = json.dumps(asdict(proj.person)), json.dumps(asdict(proj.annahmen))
    ergs = [_rechne(pj, aj, json.dumps(asdict(s))) for s in proj.szenarien]
    gueltig = [e for e in ergs if e.ok]

    with t_dash:
        for e in ergs:
            if not e.ok:
                st.error(f"**{e.szenario.name}:** " + " ".join(e.fehler))
            for w in e.warnungen:
                st.warning(f"**{e.szenario.name}:** {w}")
        if not gueltig:
            st.info("Kein gültiges Szenario – bitte Eingaben prüfen.")
        else:
            tab = E.vergleich_tabelle(gueltig, real, basis)
            st.subheader("Szenarienvergleich" + (" (real, Kaufkraft heute)" if real else " (nominal)"))
            st.dataframe(tab.set_index("Szenario"), width="stretch",
                         column_config={c: st.column_config.NumberColumn(format="%d €") for c in tab.columns
                                        if c.startswith(("Rente b", "Rente n", "Netto", "Kum", "Ausgleich"))})
            ref_name = st.selectbox("Referenzszenario für Break-even", [e.szenario.name for e in gueltig])
            ref_idx = [e.szenario.name for e in gueltig].index(ref_name)
            c1, c2 = st.columns(2)
            c1.plotly_chart(chart_monatlich(gueltig, basis, real), width="stretch")
            c2.plotly_chart(chart_kumuliert(gueltig, basis, real, ref_idx), width="stretch")
            c1, c2 = st.columns(2)
            c1.plotly_chart(chart_differenz(gueltig, basis, real, ref_idx), width="stretch")
            c2.plotly_chart(chart_depot(gueltig, real), width="stretch")

            st.subheader("Break-even-Analyse")
            st.caption("Exakt: Schnittpunkt der kumulierten Monatsreihen (lineare Interpolation im Monat, inkl. Steuer, "
                       "Sozialabgaben, Dynamisierung). Näherung: analytische Formel n = R_früh·Δ/(R_spät − R_früh) ohne Dynamik.")
            rows = []
            for e in gueltig:
                if e is gueltig[ref_idx]:
                    continue
                be = E.break_even(gueltig[ref_idx], e, basis, real)
                bv = E.break_even_vereinfacht(gueltig[ref_idx], e)
                rows.append({"Szenario": e.szenario.name,
                             "Break-even-Alter": E.fmt_alter(be["alter"] * 12) if be["alter"] else "–",
                             "Ergebnis": be["text"],
                             "Näherungsformel": bv["text"]})
            if rows:
                st.dataframe(pd.DataFrame(rows).set_index("Szenario"), width="stretch")

    with t_trace:
        if not gueltig:
            st.info("Kein gültiges Szenario.")
        else:
            e = gueltig[[x.szenario.name for x in gueltig].index(st.selectbox("Szenario", [x.szenario.name for x in gueltig], key="trace_sz"))]
            gruppen = e.trace.gruppen()
            auswahl = st.selectbox("Rechenschritte anzeigen für", gruppen)
            df = pd.DataFrame([{"Schritt": s.titel, "Formel / Herleitung": s.formel, "Wert": s.wert, "Einheit": s.einheit,
                                "Rechtsbezug / Hinweis": s.hinweis} for s in e.trace.fuer(auswahl)])
            st.dataframe(df.astype({"Wert": str}), width="stretch", hide_index=True)
            st.markdown("**Jahresübersicht (Steuer & Abzüge)**")
            jd = e.jahr.copy()
            st.dataframe(jd.round(2), width="stretch", hide_index=True)
            st.markdown("**Monatsreihen (Auszug)**")
            md = e.monat[["jahr", "monat", "alter", "gehalt_brutto", "gehalt_netto", "rente_brutto", "rente_kv", "rente_pv",
                          "steuer_rente", "rente_netto", "hinz_netto", "entnahme_netto", "luecken_kv", "depot_wert"]]
            st.dataframe(md.round(2), width="stretch", hide_index=True)
            st.download_button("Monatsreihe als CSV", e.monat.to_csv(index=False).encode("utf-8"),
                               file_name=f"{e.szenario.name}.csv", mime="text/csv")

    with t_recht:
        st.subheader("Gesetzliche Stellschrauben (aus config.py)")
        st.dataframe(pd.DataFrame(C.parameter_tabelle(), columns=["Gruppe", "Parameter", "Wert"]), hide_index=True,
                     width="stretch")
        st.markdown("**Besteuerungsanteil nach Rentenbeginn**")
        st.dataframe(pd.DataFrame({"Rentenbeginn": list(range(2020, 2061, 2)),
                                   "Besteuerungsanteil %": [C.besteuerungsanteil(j) for j in range(2020, 2061, 2)]}),
                     hide_index=True)
        st.markdown("**Altersgrenzen nach Jahrgang**")
        jg = list(range(1950, 1969))
        st.dataframe(pd.DataFrame({
            "Jahrgang": jg,
            "Regelaltersgrenze": [E.fmt_alter(C.regelaltersgrenze_monate(j)) for j in jg],
            "abschlagsfrei (45 J.)": [E.fmt_alter(C.abschlagsfrei_besonders_langjaehrig_monate(j)) for j in jg]}),
            hide_index=True)

    with t_info:
        st.markdown(METHODIK)


METHODIK = """
### Modellannahmen und bekannte Vereinfachungen
* **Besteuerungsanteil:** Nach dem Wachstumschancengesetz steigt er ab 2023 um 0,5 %-Punkte p.a. (2023: 83 %, 2025: 84 %,
  **2026: 84,5 %**, 100 % ab 2058). Der Rentenfreibetrag wird im 2. Bezugsjahr aus der dann gezahlten Jahresrente festgeschrieben;
  spätere Erhöhungen (Anpassungen, Zuschläge, Wechsel Teil→Vollrente) sind zu 100 % steuerpflichtig.
* **Steuer:** Einkommensteuer-Veranlagung des Haushalts (Grund-/Splittingtarif 2026, mit Indexierung fortgeschrieben). Die Steuer der
  Person ergibt sich per *Inkrementalmethode* (Haushaltssteuer − Steuer des Partners allein). Steuerklassen beeinflussen nur den
  Lohnsteuerabzug, nicht die Jahressteuer. Teiljahre am Simulationsrand werden annualisiert.
* **Ausgleichszahlung (§ 187a):** Preis je EP = Durchschnittsentgelt × Beitragssatz des Zahlungsjahres (Näherung; verbindlich ist die
  Auskunft der DRV). Absetzbar als Altersvorsorgeaufwendung (100 %, Höchstbetrag) – die Steuerersparnis wird mit Gehalt des Zahlungsjahres
  berechnet und in Basis 1 separat ausgewiesen.
* **KV/PV:** KVdR: Rentner trägt 7,3 % + ½ Zusatzbeitrag, PV voll. Freiwillig: Beitrag auf Rente + sonstige Einkünfte, DRV-Zuschuss
  auf die Rente. Kapitalerträge aus dem Depot werden bei freiwilliger Versicherung **nicht** beitragspflichtig modelliert.
* **Hinzuverdienst:** Minijob abgabenfrei für den AN. Darüber normaler Arbeitnehmer-Abzug; EP-Zuschläge (Zugangsfaktor 1,0 vereinfacht)
  wirken ab 1. Juli des Folgejahres. Hinzuverdienstgrenze bei Teilrente wird nicht geprüft.
* **Depot:** Jahresrendite gleichmäßig auf Monate, Abgeltungsteuer 26,375 % auf Gewinnanteil nach Teilfreistellung, Sparer-Pauschbetrag;
  Vorabpauschale und Kirchensteuer auf Kapitalerträge nicht modelliert.
* **Nicht modelliert:** Arbeitslosengeld, Rente für Schwerbehinderte, Erwerbsminderung, Betriebsrenten-Details, Hinterbliebenenrenten,
  Wohnsitz-/Ost-West-Unterschiede, Beitragssatzänderungen in der Zukunft.
* **Kumulierung:** Basis 1 = Netto-Rente − Ausgleichszahlung + deren Steuerersparnis. Basis 2 = alle Netto-Einkünfte inkl. Entnahmen.
  Basis 3 = Basis 2 + Depot-Wertveränderung (Gesamtposition). Real = durch Inflation seit Start deflationiert.

Die Parameter in `config.py` sind jährlich zu prüfen (Rentenwert, Rechengrößen, Tarif).
"""


if __name__ == "__main__":
    main()
