"""
gui.py – Geführte Streamlit-Oberfläche (Wizard).  Start:  streamlit run app.py

Ablauf: Start → 1 Du → 2 Rente → 3 Versicherung & Steuer → 4 Vermögen → 5 Szenarien → 6 Annahmen → 7 Ergebnis
Alle Eingaben liegen in ``st.session_state["store"]`` (dicts), damit sie beim Seitenwechsel erhalten bleiben.
"""
from __future__ import annotations

import json
import math
import re
from dataclasses import asdict
from datetime import date

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import config as C
import engine as E
import insights as I
from models import Annahmen, Person, Projekt, Szenario, standard_projekt

FARBEN = ["#0072B2", "#D55E00", "#009E73", "#CC79A7", "#E69F00", "#56B4E9", "#7A7A7A", "#B2182B"]
SCHRITTE = ["Start", "Du", "Rente", "Versicherung & Steuer", "Vermögen", "Szenarien", "Annahmen", "Ergebnis"]
BASIS_LABEL = {
    1: "Nur die Rente (klassischer Rentenvergleich)",
    2: "Alle Netto-Einnahmen (Gehalt, Rente, Nebenjob, Entnahmen)",
    3: "Gesamtvermögen (inkl. Depot-Entwicklung)",
}
eur = I.eur
KURZ = {"Altersrente für besonders langjährig Versicherte": "abschlagsfrei (45 J.)",
        "Altersrente für langjährig Versicherte (vorzeitig)": "vorzeitig",
        "Regelaltersrente": "Regelaltersrente", "Regelaltersrente (mit Rentenaufschub)": "mit Aufschub"}

CSS = """
<style>
.block-container{max-width:1150px;padding-top:4rem}
.hero{padding:1.6rem 1.8rem;border-radius:18px;background:linear-gradient(135deg,rgba(0,114,178,.18),rgba(0,158,115,.14));
      border:1px solid rgba(128,128,128,.25);margin-bottom:1rem}
.hero h1{margin:0 0 .4rem 0;font-size:2.1rem}
.card{border:1px solid rgba(128,128,128,.30);border-radius:14px;padding:.9rem 1.1rem;margin:.4rem 0 .8rem 0;background:rgba(128,128,128,.05)}
.card h4{margin:0 0 .3rem 0}
.why{border-left:4px solid #0072B2;padding:.5rem .9rem;margin:.4rem 0 1rem 0;background:rgba(0,114,178,.07);border-radius:0 10px 10px 0;font-size:.93rem}
.fazit{border-left:5px solid #009E73;padding:.8rem 1.1rem;margin:.4rem 0 1rem 0;background:rgba(0,158,115,.09);border-radius:0 12px 12px 0}
.pill{display:inline-block;padding:.1rem .6rem;border-radius:99px;font-size:.78rem;background:rgba(128,128,128,.2);margin-right:.3rem}
.ok{color:#009E73;font-weight:600}.bad{color:#D55E00;font-weight:600}
</style>
"""

# ============================================================================
# Zustand
# ============================================================================
def _set_projekt(pr: Projekt):
    st.session_state["store"] = {
        "person": {**asdict(pr.person), "geburtsdatum": pr.person.geburt},
        "ann": {**asdict(pr.annahmen), "start": pr.annahmen.start_datum()},
        "szen": [{**asdict(s), "id": n} for n, s in enumerate(pr.szenarien)],
        "next_id": len(pr.szenarien),
        "ui": {"real": "Nominal", "basis": 1, "alter": 85, "ref": 0, "kum_alter": 85},
    }
    for k in [k for k in st.session_state if str(k).startswith("w_")]:
        del st.session_state[k]


def S() -> dict:
    return st.session_state["store"]


def goto(i: int):
    st.session_state["step"] = i


def _proj() -> Projekt:
    st_ = S()
    pd_ = dict(st_["person"])
    pd_["geburtsdatum"] = pd_["geburtsdatum"].isoformat()
    ad_ = dict(st_["ann"])
    ad_["start"] = f"{ad_['start'].year}-{ad_['start'].month:02d}"
    return Projekt(Person.from_dict(pd_), Annahmen.from_dict(ad_), [Szenario.from_dict(s) for s in st_["szen"]])


@st.cache_data(show_spinner=False)
def _rechne(person_json: str, ann_json: str, sz_json: str) -> E.Ergebnis:
    return E.simuliere(Person.from_dict(json.loads(person_json)), Annahmen.from_dict(json.loads(ann_json)),
                       Szenario.from_dict(json.loads(sz_json)))


def rechne(p: Person, a: Annahmen, s: Szenario) -> E.Ergebnis:
    return _rechne(json.dumps(asdict(p)), json.dumps(asdict(a)), json.dumps(asdict(s)))


@st.cache_data(show_spinner="Einflussfaktoren werden berechnet …")
def _einfluss(person_json, ann_json, szs_json, idx, ref, basis, real, alter):
    return I.einfluss(Person.from_dict(json.loads(person_json)), Annahmen.from_dict(json.loads(ann_json)),
                      [Szenario.from_dict(x) for x in json.loads(szs_json)], idx, ref, basis, real, alter)


# ============================================================================
# Widget-Helfer (Werte bleiben beim Seitenwechsel erhalten)
# ============================================================================
def W(fn, label, d, field, ns, *args, **kw):
    key = f"w_{ns}_{field}"
    if key not in st.session_state:
        v = d[field]
        if fn.__name__ == "number_input":
            v = int(v) if isinstance(kw.get("step", 1.0), int) else float(v)
        st.session_state[key] = v
    d[field] = fn(label, *args, key=key, **kw)
    return d[field]


def PCT(container, label, d, field, ns, **kw):
    """Prozent-Eingabe: Anzeige in %, gespeichert als Anteil."""
    key = f"w_{ns}_{field}"
    if key not in st.session_state:
        st.session_state[key] = round(float(d[field]) * 100, 3)
    v = container.number_input(label, key=key, step=0.5, format="%.1f", min_value=-5.0, max_value=100.0, **kw)
    d[field] = v / 100
    return d[field]


def ALTER(container, label, d, field, ns, minj, maxj, help=None):
    kj, km = f"w_{ns}_{field}_j", f"w_{ns}_{field}_m"
    if kj not in st.session_state:
        st.session_state[kj], st.session_state[km] = int(d[field] // 12), int(d[field] % 12)
    container.markdown(f"**{label}**", help=help)
    c1, c2 = container.columns(2)
    j = c1.number_input("Jahre", key=kj, min_value=minj, max_value=maxj, step=1)
    m = c2.number_input("Monate", key=km, min_value=0, max_value=11, step=1)
    d[field] = int(j) * 12 + int(m)


def WHY(text: str):
    st.markdown(f'<div class="why">💡 {text}</div>', unsafe_allow_html=True)


# ============================================================================
# Navigation
# ============================================================================
def stepper():
    cur = st.session_state["step"]
    cols = st.columns(len(SCHRITTE))
    for i, (c, n) in enumerate(zip(cols, SCHRITTE)):
        mark = "✓ " if i < cur and i > 0 else ""
        c.button(f"{mark}{i}. {n}" if i else "Start", key=f"nav{i}", on_click=goto, args=(i,),
                 type="primary" if i == cur else "secondary", width="stretch")
    st.progress(cur / (len(SCHRITTE) - 1))


def nav_unten():
    cur = st.session_state["step"]
    st.divider()
    c1, _, c2 = st.columns([1, 3, 1])
    if cur > 0:
        c1.button("← Zurück", on_click=goto, args=(cur - 1,), key="back", width="stretch")
    if cur < len(SCHRITTE) - 1:
        label = "Ergebnis ansehen →" if cur == len(SCHRITTE) - 2 else "Weiter →"
        c2.button(label, on_click=goto, args=(cur + 1,), key="next", type="primary", width="stretch")


# ============================================================================
# Seiten
# ============================================================================
def page_start():
    st.markdown("""<div class="hero"><h1>📈 Wann in Rente – und was bringt es wirklich?</h1>
    Vergleiche mehrere Rentenszenarien (früh mit Abschlag, abschlagsfrei, regulär, Teilrente, Überbrückung mit Ersparnissen)
    <b>nach Steuern und Sozialabgaben</b> – und finde heraus, ab welchem Alter sich ein späterer Rentenbeginn auszahlt.</div>""",
                unsafe_allow_html=True)
    c = st.columns(3)
    c[0].markdown('<div class="card"><h4>1 · Angaben</h4>Geburtsdatum, Rentenauskunft, Familie, Ersparnisse. '
                  'Zu jedem Feld erfährst du, warum es wichtig ist und was es bewirkt.</div>', unsafe_allow_html=True)
    c[1].markdown('<div class="card"><h4>2 · Szenarien</h4>Wähle Vorlagen oder stelle eigene Varianten zusammen – '
                  'mit Klartext-Beschreibung, was jede Variante bedeutet.</div>', unsafe_allow_html=True)
    c[2].markdown('<div class="card"><h4>3 · Ergebnis</h4>Fazit in Worten, Diagramme, Break-even und eine '
                  'Analyse, welche Annahmen das Ergebnis am stärksten beeinflussen.</div>', unsafe_allow_html=True)
    b1, b2, _ = st.columns([1, 1.4, 2])
    b1.button("Los geht's →", type="primary", on_click=goto, args=(1,), width="stretch")
    b2.button("Erst mal mit Beispieldaten ansehen", on_click=lambda: (_set_projekt(standard_projekt()), goto(7)), width="stretch")
    st.caption("Das Tool ist eine Modellrechnung und ersetzt keine Rentenberatung, Steuerberatung oder Anlageberatung. "
               "Gespeichert wird nichts auf einem Server – du kannst dein Projekt oben links als Datei sichern.")


def page_du():
    p = S()["person"]
    st.header("1 · Über dich")
    c1, c2 = st.columns(2)
    with c1:
        W(st.date_input, "Geburtsdatum", p, "geburtsdatum", "p", min_value=date(1940, 1, 1), max_value=date(2010, 12, 31),
          format="DD.MM.YYYY", help="Daraus ergeben sich Regelaltersgrenze, abschlagsfreies Alter und die Steuer-Kohorte.")
        W(st.number_input, "Aktuelles Bruttojahresgehalt (€)", p, "brutto_jahr", "p", min_value=0.0, step=1000.0,
          help="Dein Gehalt bis zum Ende der Erwerbsphase – bestimmt Netto in den Arbeitsjahren und die Steuerersparnis einer Ausgleichszahlung.")
    with c2:
        verh = st.radio("Familienstand", ["Alleinstehend", "Verheiratet / eingetragene Partnerschaft"],
                        index=1 if p["steuerklasse"] in C.SPLITTING_KLASSEN else 0, key="w_verh",
                        help="Verheiratete werden gemeinsam veranlagt (Splittingtarif) – das senkt die Steuer oft deutlich.")
        if verh.startswith("Allein"):
            p["steuerklasse"] = "1"
        else:
            kl = [k for k in C.STEUERKLASSEN if k in C.SPLITTING_KLASSEN]
            if p["steuerklasse"] not in kl:
                p["steuerklasse"] = "4"
            W(st.selectbox, "Steuerklasse", p, "steuerklasse", "p", kl, format_func=lambda k: C.STEUERKLASSEN[k],
              help="Die Klasse bestimmt nur den monatlichen Lohnsteuerabzug. Für die endgültige Steuer zählt die gemeinsame Veranlagung.")
    gj = p["geburtsdatum"].year
    rag, bl = C.regelaltersgrenze_monate(gj), C.abschlagsfrei_besonders_langjaehrig_monate(gj)
    st.markdown("##### Deine Rentenaltersgrenzen")
    m = st.columns(3)
    m[0].metric("Regelaltersgrenze", E.fmt_alter(rag), help="Ab hier gibt es die Rente ohne Abschlag (mind. 5 Beitragsjahre).")
    m[1].metric("Früheste Rente (63)", "63 J. 0 M.", f"−{min(C.MAX_ABSCHLAG_MONATE, rag - 756) * 0.3:.1f} % Rente",
                delta_color="inverse", delta_arrow="down", help="Mit 35 Versicherungsjahren. 0,3 % Abschlag je Monat, max. 14,4 % – dauerhaft.")
    m[2].metric("Abschlagsfrei mit 45 Jahren", E.fmt_alter(bl), help="Altersrente für besonders langjährig Versicherte.")
    WHY("<b>Einfluss:</b> Jeder Monat früher als die Regelaltersgrenze kostet <b>0,3 % Rente – ein Leben lang</b>. "
        "Das Geburtsjahr legt fest, wie viele Monate das maximal sind.")
    kids = st.text_input("Geburtsjahre deiner Kinder (kommagetrennt, leer = keine Kinder)",
                         value=", ".join(str(x) for x in p["kinder_geburtsjahre"]), key="w_p_kids",
                         help="Steuert in der Pflegeversicherung den Kinderlosenzuschlag (+0,6 %) und Abschläge ab dem 2. Kind unter 25.")
    p["kinder_geburtsjahre"] = [int(x) for x in kids.replace(";", ",").split(",") if x.strip().isdigit()]
    if p["steuerklasse"] in C.SPLITTING_KLASSEN:
        st.markdown("##### Dein Partner / deine Partnerin")
        WHY("<b>Einfluss:</b> Im Splittingtarif wird das gemeinsame Einkommen halbiert, besteuert und verdoppelt. "
            "Je ungleicher die Einkommen, desto größer der Vorteil. Wir rechnen nur die <i>zusätzliche</i> Steuer, die durch dein Einkommen entsteht.")
        c = st.columns(3)
        W(c[0].number_input, "Einkünfte p.a. (zu versteuern)", p, "partner_einkuenfte_jahr", "p", min_value=0.0, step=1000.0,
          help="Steuerpflichtige Einkünfte: bei Gehalt Brutto minus Werbungskosten, bei Rente nur der steuerpflichtige Anteil.")
        W(c[1].number_input, "Abziehbare Vorsorge p.a.", p, "partner_vorsorge_jahr", "p", min_value=0.0, step=500.0,
          help="Kranken-/Pflegeversicherungsbeiträge des Partners.")
        PCT(c[2], "Einkommenssteigerung p.a. (%)", p, "partner_wachstum", "p")
    with st.expander("Weitere Angaben (optional)"):
        W(st.selectbox, "Kirchensteuer", p, "kirchensteuer", "p", [0.0, 0.08, 0.09], format_func=lambda x: "keine" if x == 0 else f"{x:.0%}")


def page_rente():
    p = S()["person"]
    st.header("2 · Deine Rentenansprüche")
    WHY("Diese Zahlen findest du in deiner <b>Rentenauskunft</b> (Deutsche Rentenversicherung, auch im Online-Konto). "
        "Aus Entgeltpunkten (EP) × Rentenwert ergibt sich deine Monatsrente: <b>1 EP ≈ "
        f"{C.RENTENWERT_AB_JULI_2026:.2f} € Rente pro Monat</b> (brutto).")
    c1, c2 = st.columns(2)
    with c1:
        W(st.number_input, "Entgeltpunkte bisher", p, "ep_aktuell", "p", min_value=0.0, step=0.5, format="%.3f",
          help="Rentenauskunft → 'bisher erworbene Entgeltpunkte'.")
    with c2:
        W(st.number_input, "Erwartete Entgeltpunkte je weiterem Arbeitsjahr", p, "ep_pro_jahr", "p", min_value=0.0, step=0.1, format="%.3f")
        vorschlag = min(p["brutto_jahr"], C.BBG_RV_JAHR) / C.DURCHSCHNITTSENTGELT_JAHR
        st.caption(f"Faustformel: Jahresbrutto ÷ Durchschnittsentgelt ({C.DURCHSCHNITTSENTGELT_JAHR:,.0f} €) = **{vorschlag:.2f}**")
        if st.button("Vorschlag übernehmen", key="ep_vorschlag"):
            p["ep_pro_jahr"] = round(vorschlag, 3)
            st.session_state["w_p_ep_pro_jahr"] = round(vorschlag, 3)
            st.rerun()
    st.markdown("##### Wartezeiten")
    WHY("Für die vorzeitige Rente brauchst du <b>35 Jahre</b>, für die abschlagsfreie Rente vor der Regelaltersgrenze <b>45 Jahre</b> "
        "anrechenbare Zeiten (Pflichtbeiträge, Kindererziehung, Pflege, Teile von Arbeitslosigkeit …). Die Rentenauskunft nennt dir die Zahlen.")
    c1, c2 = st.columns(2)
    W(c1.number_input, "Jahre für die 35-Jahre-Wartezeit (heute)", p, "wartezeit_jahre_35", "p", min_value=0.0, step=0.5)
    W(c2.number_input, "Jahre für die 45-Jahre-Wartezeit (heute)", p, "wartezeit_jahre_45", "p", min_value=0.0, step=0.5,
      help="Zählt strenger: z. B. Arbeitslosigkeit in den letzten 2 Jahren vor Rentenbeginn zählt nicht mit.")
    heute = date.today()
    for lbl, need, have, col in (("35 Jahre", 35, p["wartezeit_jahre_35"], c1), ("45 Jahre", 45, p["wartezeit_jahre_45"], c2)):
        rest = max(0.0, need - have)
        alter_m = (heute.year - p["geburtsdatum"].year) * 12 + heute.month - p["geburtsdatum"].month + round(rest * 12)
        col.caption(("✅ bereits erfüllt" if rest == 0 else f"Bei durchgehender Arbeit erreicht ab Alter **{E.fmt_alter(alter_m)}**") + f" ({lbl})")
    # Hochrechnung
    st.markdown("##### Grobe Hochrechnung deiner Rente")
    pr = _proj()
    gj = pr.person.geburt.year
    rag, frueh = C.regelaltersgrenze_monate(gj), C.FRUEHESTENS_LANGJAEHRIG_MONATE
    m = st.columns(2)
    for col, alter, txt in ((m[0], rag, "zur Regelaltersgrenze"), (m[1], frueh, "mit 63 (vorzeitig)")):
        e = rechne(pr.person, pr.annahmen, Szenario(name="x", erwerbsende_alter_m=alter, rentenbeginn_alter_m=alter))
        if e.ok:
            col.metric(f"Bruttorente {txt}", eur(e.kennzahlen["rente_brutto_start"]) + "/Monat",
                       f"Netto ca. {eur(e.kennzahlen['rente_netto_start'])}", delta_color="off",
                       help="Nominal, im Jahr des Rentenbeginns (inkl. künftiger Rentenanpassungen).")
        else:
            col.warning(f"{txt}: nicht möglich – " + " ".join(e.fehler))


def page_vers():
    p = S()["person"]
    st.header("3 · Krankenversicherung & weitere Einkünfte")
    WHY("Von deiner Rente gehen <b>Kranken- und Pflegeversicherung</b> ab, danach ggf. Steuer. Als Pflichtmitglied der "
        "Krankenversicherung der Rentner (KVdR) zahlst du ca. 8,75 % (inkl. Zusatzbeitrag) Kranken- und 3,6 % Pflegebeitrag (Kinderlose 4,2 %). "
        "Freiwillig Versicherte zahlen zusätzlich auf <i>alle</i> Einkünfte (Mieten, Betriebsrenten …).")
    c1, c2 = st.columns(2)
    with c1:
        W(st.number_input, "Zeit in der gesetzlichen KV in der 2. Hälfte des Erwerbslebens (%)", p, "gkv_anteil_zweite_haelfte", "p",
          min_value=0.0, max_value=100.0, step=5.0,
          help="9/10-Regel: Wer mindestens 90 % dieser Zeit gesetzlich (auch familien-)versichert war, kommt in die günstige KVdR. "
               "Privat Versicherte/Selbstständige meist nicht.")
        mod, grund = E.kv_modus_effektiv(Person.from_dict({**p, "geburtsdatum": p["geburtsdatum"].isoformat()}))
        (st.success if mod == "kvdr" else st.warning)(grund)
    with c2:
        W(st.selectbox, "Krankenversicherung im Ruhestand", p, "kv_modus", "p", ["auto", "kvdr", "freiwillig"],
          format_func={"auto": "Automatisch (nach 9/10-Prüfung)", "kvdr": "KVdR erzwingen", "freiwillig": "Freiwillige GKV erzwingen"}.get,
          help="Normalfall: automatisch.")
        W(st.checkbox, "In einer Erwerbslücke über den Partner familienversichert", p, "luecken_kv_familienversichert", "p",
          help="Wenn du zwischen Erwerbsende und Rentenbeginn nichts verdienst, fallen sonst freiwillige KV-Beiträge an (min. ca. 18 % auf 1.318 €).")
    st.markdown("##### Weitere steuerpflichtige Einkünfte")
    W(st.number_input, "Sonstige Einkünfte pro Jahr (z. B. Mieten, Betriebsrente)", p, "sonstige_einkuenfte_jahr", "p",
      min_value=0.0, step=500.0, help="Voll steuerpflichtig, wachsen mit der Inflation. Erhöhen die Steuerlast auf deine Rente (Progression).")
    WHY("<b>Einfluss:</b> Zusätzliche Einkünfte werden auf deine Rente „obendrauf“ besteuert – dadurch rutscht auch die Rente in einen höheren Steuersatz.")


def page_verm():
    p = S()["person"]
    st.header("4 · Ersparnisse zur Überbrückung")
    WHY("Wer früher aufhört, als die Rente beginnt, braucht Geld für die Lücke. Hier hinterlegst du dein Depot/Tagesgeld. "
        "Entnahmen werden mit Abgeltungsteuer (26,375 %) auf den Gewinnanteil belastet. Ohne Ersparnisse einfach <b>0</b> lassen.")
    c = st.columns(2)
    W(c[0].number_input, "Depotwert heute (€)", p, "depot_start", "p", min_value=0.0, step=5000.0)
    PCT(c[1], "Erwartete Rendite p.a. (%, nominal nach Kosten)", p, "depot_rendite", "p")
    if p["depot_start"] > 0:
        with st.expander("Steuer-Details zum Depot", expanded=True):
            c = st.columns(2)
            W(c[0].slider, "Anteil Kursgewinne am Depotwert", p, "depot_gewinnanteil", "p", 0.0, 1.0, step=0.05,
              help="0 % = nur Eingezahltes (keine Steuer), 100 % = alles Gewinn.")
            W(c[1].slider, "Teilfreistellung", p, "depot_teilfreistellung", "p", 0.0, 1.0, step=0.05,
              help="30 % bei Aktienfonds, 15 % bei Mischfonds, 0 % bei Tagesgeld/Anleihen.")
        WHY("<b>Einfluss:</b> Das Depot wächst auch während der Überbrückung weiter. Szenarien mit Entnahmen verbrauchen Kapital, "
            "frühere Rente verbraucht keins – das zeigt der „Gesamtvermögen“-Vergleich im Ergebnis.")


def _szenario_karte(sd: dict, idx: int, pr: Projekt):
    sid = sd["id"]
    s = Szenario.from_dict(sd)
    e = rechne(pr.person, pr.annahmen, s)
    with st.container(border=True):
        h1, h2, h3, h4 = st.columns([5, 1, 1, 1])
        sd["name"] = h1.text_input("Name", value=sd["name"], key=f"w_s{sid}_name", label_visibility="collapsed")
        if h2.button("⧉", key=f"dup{sid}", help="Duplizieren"):
            S()["szen"].append({**sd, "id": S()["next_id"], "name": sd["name"] + " (Kopie)"})
            S()["next_id"] += 1
            st.rerun()
        if h3.button("🗑", key=f"del{sid}", help="Löschen"):
            S()["szen"].remove(sd)
            for k in [k for k in st.session_state if str(k).startswith(f"w_s{sid}_")]:
                del st.session_state[k]
            st.rerun()
        h4.markdown(f"<span style='color:{FARBEN[idx % len(FARBEN)]};font-size:1.6rem'>●</span>", unsafe_allow_html=True)
        if e.ok:
            st.markdown(I.beschreibe(s, e))
        else:
            st.markdown(I.beschreibe(s))
            st.error(" ".join(e.fehler))
        for w in (e.warnungen if e.ok else []):
            st.warning(w)
        with st.expander("Bearbeiten"):
            st.markdown("**Zeitpunkte**")
            c = st.columns(2)
            ALTER(c[0], "Letzter Arbeitstag mit Alter", sd, "erwerbsende_alter_m", f"s{sid}", 50, 75,
                  help="Ab hier fällt dein Gehalt weg. Liegt der Rentenbeginn später, entsteht eine Lücke, die du ggf. aus Ersparnissen deckst.")
            ALTER(c[1], "Rentenbeginn mit Alter", sd, "rentenbeginn_alter_m", f"s{sid}", 60, 75,
                  help="Die Rentenart (vorzeitig/abschlagsfrei/regulär) und der Abschlag werden automatisch bestimmt.")
            t1, t2, t3 = st.tabs(["Teilrente & Nebenjob", "Abschlag ausgleichen (§ 187a)", "Lücke aus Ersparnissen"])
            with t1:
                WHY("<b>Teilrente:</b> Du beziehst nur einen Teil der Rente und arbeitest weiter; später wechselst du auf die volle Rente. "
                    "<b>Nebenjob:</b> bis 603 € im Monat abgabenfrei (Minijob), darüber normal sozialversicherungspflichtig – dann sammelst du weitere Rentenpunkte.")
                c = st.columns(3)
                W(c[0].number_input, "Teilrente in % (100 = voll)", sd, "teilrente_prozent", f"s{sid}", min_value=10.0, max_value=100.0, step=10.0)
                ALTER(c[1], "Wechsel auf Vollrente (0 = nie)", sd, "vollrente_alter_m", f"s{sid}", 0, 75)
                W(c[2].number_input, "Nebenjob brutto/Monat (heutige Kaufkraft)", sd, "hinzuverdienst_monat", f"s{sid}", min_value=0.0, step=50.0)
                c = st.columns(2)
                ALTER(c[0], "Nebenjob bis Alter", sd, "hinzuverdienst_bis_alter_m", f"s{sid}", 55, 80)
                W(c[1].checkbox, "RV-Beiträge zahlen (bringt Rentenpunkte)", sd, "rv_aufstocken", f"s{sid}")
            with t2:
                WHY("Mit einer <b>Sonderzahlung ab 50</b> kaufst du den Abschlag zurück. Sie ist als Altersvorsorgeaufwendung <b>steuerlich absetzbar</b> "
                    "(bis zum Höchstbetrag) – verteilt auf mehrere Jahre sparst du oft mehr Steuern. Nur sinnvoll bei vorzeitigem Rentenbeginn mit Abschlag. "
                    "Der exakte Betrag kommt von der DRV; hier wird er näherungsweise berechnet.")
                c = st.columns(3)
                W(c[0].selectbox, "Ausgleich", sd, "ausgleich_modus", f"s{sid}", ["keine", "voll", "betrag"],
                  format_func={"keine": "Keine Zahlung", "voll": "Abschlag voll ausgleichen", "betrag": "Fester Betrag"}.get)
                W(c[1].number_input, "Gesamtbetrag (€)", sd, "ausgleich_betrag", f"s{sid}", min_value=0.0, step=1000.0,
                  disabled=sd["ausgleich_modus"] != "betrag")
                W(c[2].number_input, "Verteilt auf … Jahre", sd, "ausgleich_jahre", f"s{sid}", min_value=1, max_value=10, step=1)
            with t3:
                WHY("Liegt dein Rentenbeginn nach dem Arbeitsende, entnimmst du aus dem Depot so viel, dass dein <b>Wunsch-Netto</b> erreicht wird. "
                    "Reicht das Depot nicht, siehst du die Lücke als Warnung.")
                c = st.columns(2)
                W(c[0].number_input, "Wunsch-Nettoeinkommen/Monat (heutige Kaufkraft, 0 = keine Entnahme)", sd, "wunsch_netto_monat", f"s{sid}",
                  min_value=0.0, step=100.0)
                W(c[1].selectbox, "Entnahme", sd, "entnahme_modus", f"s{sid}", ["bis_rente", "dauerhaft"],
                  format_func={"bis_rente": "Nur bis zum Rentenbeginn (Brücke)", "dauerhaft": "Dauerhaft, falls Rente unter Wunsch-Netto"}.get)


def page_szen():
    st.header("5 · Szenarien zusammenstellen")
    WHY("Ein Szenario ist eine Variante deines Ruhestands. Starte mit Vorlagen und passe sie an. "
        "Wir empfehlen <b>3–5 Szenarien</b> – das erste dient später als Referenz für den Break-even.")
    pr = _proj()
    gj = pr.person.geburt.year
    vl = I.vorlagen(gj)
    c1, c2 = st.columns([3, 1])
    wahl = c1.selectbox("Vorlage hinzufügen", list(vl.keys()), key="w_vorlage")
    if c2.button("➕ Hinzufügen", width="stretch", key="addv"):
        S()["szen"].append({**asdict(vl[wahl]), "id": S()["next_id"]})
        S()["next_id"] += 1
        st.rerun()
    if not S()["szen"]:
        st.info("Noch kein Szenario – füge oben eine Vorlage hinzu.")
    for i, sd in enumerate(list(S()["szen"])):
        _szenario_karte(sd, i, pr)


def page_ann():
    a = S()["ann"]
    st.header("6 · Annahmen über die Zukunft")
    WHY("Niemand kennt die Zukunft. Die Voreinstellungen sind plausible Mittelwerte. Im Ergebnis siehst du unter "
        "<b>„Einflussfaktoren“</b>, welche dieser Annahmen dein Ergebnis wirklich verändern.")
    p1, p2, p3, _ = st.columns([1, 1, 1, 3])
    for col, name, vals in ((p1, "Vorsichtig", (0.01, 0.025, 0.02)), (p2, "Standard", (0.02, 0.02, 0.03)), (p3, "Optimistisch", (0.03, 0.015, 0.035))):
        if col.button(name, key=f"preset{name}", width="stretch"):
            a["rentensteigerung"], a["inflation"], a["lohnsteigerung"] = vals
            for f in ("rentensteigerung", "inflation", "lohnsteigerung"):
                st.session_state.pop(f"w_a_{f}", None)
            st.rerun()
    c = st.columns(3)
    with c[0]:
        PCT(c[0], "Rentenanpassung p.a. (%)", a, "rentensteigerung", "a",
            help="Jährliche Erhöhung zum 1. Juli. Je höher, desto mehr lohnt sich ein späterer Beginn kaum – aber alle Renten steigen.")
        PCT(c[0], "Inflation p.a. (%)", a, "inflation", "a",
            help="Bestimmt die „reale“ Kaufkraft. Liegt die Inflation über der Rentenanpassung, verliert die Rente real an Wert.")
    with c[1]:
        PCT(c[1], "Gehaltssteigerung p.a. (%)", a, "lohnsteigerung", "a",
            help="Gehalt, Beitragsbemessungsgrenzen und Durchschnittsentgelt. Beeinflusst vor allem Ausgleichszahlung und Nebenjob.")
        PCT(c[1], "Fortschreibung Steuertarif p.a. (%)", a, "tarif_indexierung", "a",
            help="0 % = kalte Progression (Steuerlast steigt real). Üblich: Anpassung etwa in Höhe der Inflation.")
    with c[2]:
        PCT(c[2], "KV-Zusatzbeitrag (%)", a, "zusatzbeitrag", "a", help="Dein Kassen-Zusatzbeitrag (Ø 2026: 2,9 %).")
        W(st.number_input, "Betrachtung bis Alter", a, "horizont_alter", "a", min_value=80, max_value=105, step=1)
    WHY("<b>Faustregel:</b> Rentenanpassung ≈ Inflation → die Kaufkraft deiner Rente bleibt stabil. "
        "Liegt die Anpassung deutlich darunter, ist die Rente später real weniger wert.")
    with st.expander("Simulationsstart"):
        W(st.date_input, "Heute (Startmonat der Rechnung)", a, "start", "a", format="DD.MM.YYYY")


# ============================================================================
# Diagramme
# ============================================================================
def _layout(fig: go.Figure, titel: str, ytitel: str) -> go.Figure:
    fig.update_layout(title=titel, hovermode="x unified", height=430, margin=dict(l=10, r=10, t=50, b=10),
                      xaxis_title="Lebensalter", yaxis_title=ytitel, legend=dict(orientation="h", y=-0.2), yaxis_tickformat=",.0f")
    return fig


def chart_monatlich(ergs, basis, real):
    fig = go.Figure()
    for n, e in enumerate(ergs):
        s = E.serie_monatlich(e, basis, real)
        fig.add_trace(go.Scatter(x=s.index, y=s.values, name=e.szenario.name, mode="lines",
                                 line=dict(color=FARBEN[n % len(FARBEN)], width=2.5), hovertemplate="%{y:,.0f} €"))
    return _layout(fig, "Monatliches Netto in heutiger Kaufkraft" if real else "Monatliches Netto (nominal)", "€ pro Monat")


def chart_kumuliert(ergs, basis, real, ref_idx):
    fig = go.Figure()
    for n, e in enumerate(ergs):
        s = E.serie_kumuliert(e, basis, real)
        fig.add_trace(go.Scatter(x=s.index, y=s.values, name=e.szenario.name, mode="lines",
                                 line=dict(color=FARBEN[n % len(FARBEN)], width=2.5), hovertemplate="%{y:,.0f} €"))
    for n, e in enumerate(ergs):
        if n == ref_idx:
            continue
        be = E.break_even(ergs[ref_idx], e, basis, real)
        if be["status"] == "ok":
            s = E.serie_kumuliert(e, basis, real)
            y = float(s.iloc[(s.index >= be["alter"]).argmax()])
            fig.add_trace(go.Scatter(x=[be["alter"]], y=[y], mode="markers", showlegend=False,
                                     marker=dict(size=11, symbol="diamond", color=FARBEN[n % len(FARBEN)], line=dict(width=2, color="white")),
                                     hovertemplate=f"Break-even {e.szenario.name}: {be['alter']:.1f} J.<extra></extra>"))
    return _layout(fig, "Kumuliertes Netto seit heute (◆ = Break-even)", "€ summiert")


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
    return _layout(fig, f"Vorsprung gegenüber „{ergs[ref_idx].szenario.name}“", "€ Differenz")


def chart_depot(ergs, real):
    fig = go.Figure()
    for n, e in enumerate(ergs):
        m = e.monat
        fig.add_trace(go.Scatter(x=m["alter"], y=m["depot_wert"] / (m["deflator"] if real else 1), name=e.szenario.name,
                                 line=dict(color=FARBEN[n % len(FARBEN)], width=2.5), hovertemplate="%{y:,.0f} €"))
    return _layout(fig, "Depotverlauf", "€")


def chart_wasserfall(df: pd.DataFrame, name: str, ref_name: str):
    fig = go.Figure(go.Waterfall(
        orientation="v", measure=["relative"] * len(df) + ["total"], x=list(df["Komponente"]) + ["Vorsprung gesamt"],
        y=list(df["Δ"]) + [0], text=[f"{v:+,.0f} €".replace(",", ".") for v in df["Δ"]] + [f"{df['Δ'].sum():+,.0f} €".replace(",", ".")],
        textposition="outside", connector=dict(line=dict(color="#999", width=1)),
        increasing=dict(marker=dict(color="#009E73")), decreasing=dict(marker=dict(color="#D55E00")),
        totals=dict(marker=dict(color="#0072B2")), hovertemplate="%{y:,.0f} €<extra></extra>"))
    fig.update_layout(title=f"„{name}“ gegenüber „{ref_name}“: Woher kommt der Unterschied?", height=480,
                      margin=dict(l=10, r=10, t=60, b=10), yaxis_title="€ (Unterschied)", yaxis_tickformat=",.0f", showlegend=False)
    fig.update_xaxes(tickangle=-20)
    return fig


def chart_tornado(df: pd.DataFrame, titel: str):
    fig = go.Figure()
    labels = [f"{r.Faktor}<br><sub>jetzt {r.aktuell} · blau {r.niedrig} · orange {r.hoch}</sub>" for r in df.itertuples()]
    fig.add_trace(go.Bar(y=labels, x=df["Δ niedrig"], orientation="h", name="Annahme niedriger", marker_color="#56B4E9",
                         hovertemplate="%{x:,.0f} €"))
    fig.add_trace(go.Bar(y=labels, x=df["Δ hoch"], orientation="h", name="Annahme höher", marker_color="#D55E00",
                         hovertemplate="%{x:,.0f} €"))
    fig.update_layout(barmode="relative", title=titel, height=120 + 70 * len(df), margin=dict(l=10, r=10, t=50, b=10),
                      xaxis_title="← schlechter für das Szenario | besser für das Szenario →", xaxis_tickformat=",.0f", legend=dict(orientation="h", y=-0.15))
    return fig


# ============================================================================
# Ergebnis
# ============================================================================
LIVE_CSS = """
<style>
.block-container{max-width:1500px}
div[data-testid="stHorizontalBlock"]:has(> div[data-testid="stColumn"] .livepanel){align-items:flex-start}
div[data-testid="stColumn"]:has(.livepanel){position:sticky;top:3.2rem;max-height:calc(100vh - 3.8rem);overflow-y:auto;
    padding:.2rem .6rem .6rem .2rem}
</style>
"""


def _snap_ann(ui: dict) -> dict:
    return ui.setdefault("snap_ann", {"rentensteigerung": S()["ann"]["rentensteigerung"], "inflation": S()["ann"]["inflation"]})


def live_controls():
    """Live-Regler für das Master-Szenario (schreiben direkt in den Store)."""
    ui, szs, a = S()["ui"], S()["szen"], S()["ann"]
    st.markdown('<div class="livepanel"></div>', unsafe_allow_html=True)
    st.subheader("🎛 Live-Spielwiese")
    st.caption("Ändere Werte des Master-Szenarios – Kennzahlen und alle Diagramme rechts reagieren sofort.")
    if not szs:
        st.info("Noch kein Szenario angelegt.")
        return None, None
    ui["master"] = min(ui.get("master", 0), len(szs) - 1)
    mi = W(st.selectbox, "Master-Szenario", ui, "master", "ui", list(range(len(szs))), format_func=lambda i: szs[i]["name"])
    kpi_box = st.container()      # wird später mit den Kennzahlen gefüllt – steht oben, Regler darunter
    sd = szs[mi]
    sid = sd["id"]
    ui.setdefault("snap", {}).setdefault(sid, dict(sd))
    _snap_ann(ui)
    ss = st.session_state

    def init(key, val):
        if key not in ss:
            ss[key] = val

    def clamp(v, lo, hi):
        return max(lo, min(hi, v))

    k = f"w_live_{sid}_"
    init(k + "ew", clamp(int(sd["erwerbsende_alter_m"]), 600, 840))
    init(k + "rb", clamp(int(sd["rentenbeginn_alter_m"]), 720, 840))
    sd["erwerbsende_alter_m"] = st.select_slider("Letzter Arbeitstag mit …", options=list(range(600, 841)), format_func=E.fmt_alter, key=k + "ew")
    sd["rentenbeginn_alter_m"] = st.select_slider("Rentenbeginn mit …", options=list(range(720, 841)), format_func=E.fmt_alter, key=k + "rb",
                                                    help="Jeder Monat früher kostet 0,3 % Rente (max. 14,4 %), jeder Monat später nach der Regelaltersgrenze bringt 0,5 % Zuschlag.")
    init(k + "tr", clamp(int(round(sd["teilrente_prozent"] / 10) * 10), 10, 100))
    sd["teilrente_prozent"] = float(st.select_slider("Teilrente (%)", options=list(range(10, 101, 10)), key=k + "tr",
                                                       help="100 = volle Rente. Bei weniger startet die Restrente ggf. später (im Szenario-Editor festlegbar)."))
    init(k + "hz", int(clamp(round(sd["hinzuverdienst_monat"] / 50) * 50, 0, 2000)))
    sd["hinzuverdienst_monat"] = float(st.slider("Nebenjob brutto/Monat (€)", 0, 2000, step=50, key=k + "hz"))
    modi = ["keine", "voll"] + (["betrag"] if sd["ausgleich_modus"] == "betrag" else [])
    init(k + "au", sd["ausgleich_modus"])
    sd["ausgleich_modus"] = st.radio("Abschlag ausgleichen (§ 187a)", modi, horizontal=True, key=k + "au",
                                     format_func={"keine": "Nein", "voll": "Voll", "betrag": "Fester Betrag"}.get)
    init(k + "wn", int(clamp(round(sd["wunsch_netto_monat"] / 100) * 100, 0, 6000)))
    sd["wunsch_netto_monat"] = float(st.slider("Wunsch-Netto aus Ersparnissen (€/Monat)", 0, 6000, step=100, key=k + "wn",
                                               help="0 = keine Entnahme aus dem Depot."))
    st.markdown("**Annahmen**")
    init("w_live_rs", round(a["rentensteigerung"] * 100, 1))
    init("w_live_inf", round(a["inflation"] * 100, 1))
    a["rentensteigerung"] = st.slider("Rentenanpassung p.a. (%)", 0.0, 5.0, step=0.1, key="w_live_rs") / 100
    a["inflation"] = st.slider("Inflation p.a. (%)", 0.0, 6.0, step=0.1, key="w_live_inf") / 100
    b1, b2 = st.columns(2)
    if b1.button("↺ Zurücksetzen", key="live_reset", width="stretch", help="Zurück auf die Werte, mit denen du das Ergebnis geöffnet hast."):
        sd.update(ui["snap"][sid])
        a.update(ui["snap_ann"])
        for kk in [kk for kk in ss if str(kk).startswith("w_live_") or str(kk).startswith(f"w_s{sid}_")]:
            del ss[kk]
        st.rerun()
    if b2.button("➕ Als Szenario", key="live_save", width="stretch", help="Aktuelle Einstellung als neues Szenario festhalten."):
        szs.append({**sd, "id": S()["next_id"], "name": sd["name"] + " (Variante)"})
        S()["next_id"] += 1
        st.rerun()
    return mi, kpi_box


def live_kennzahlen(mi: int | None, ergs_all, pr: Projekt, basis: int, real: bool, ref_name: str, alter: int):
    """Kennzahlen + Mini-Chart des Master-Szenarios im Vergleich zum Zustand beim Öffnen (Δ)."""
    if mi is None:
        return
    ui = S()["ui"]
    sd = S()["szen"][mi]
    e = ergs_all[mi]
    if not e.ok:
        st.error(" ".join(e.fehler))
        return
    ann0 = Annahmen.from_dict({**asdict(pr.annahmen), **ui["snap_ann"]})
    s0 = Szenario.from_dict(ui["snap"][sd["id"]])
    e0 = rechne(pr.person, ann0, s0)
    ref_s = next((x for x in pr.szenarien if x.name == ref_name), None)
    suf = "_real" if real else ""
    k, k0 = e.kennzahlen, (e0.kennzahlen if e0.ok else None)
    st.metric("Netto-Rente / Monat", eur(k["rente_netto_start" + suf]),
              delta=eur(k["rente_netto_start" + suf] - k0["rente_netto_start" + suf]) if k0 else None, help="Ø im 1. vollen Rentenjahr. Δ = Änderung seit Öffnen des Ergebnisses.")
    c1, c2 = st.columns(2)
    c1.metric("Abschlag", f"{k['abschlag_prozent']:.1f} %", delta=f"{k['abschlag_prozent'] - k0['abschlag_prozent']:+.1f} %" if k0 else None, delta_color="inverse")
    km = I._kum(e, basis, real, alter)
    c2.metric(f"Summe bis {alter}", eur(km), delta=eur(km - I._kum(e0, basis, real, alter)) if k0 else None)
    if ref_s is not None and ref_s.name != sd["name"]:
        er, er0 = rechne(pr.person, pr.annahmen, ref_s), rechne(pr.person, ann0, ref_s)
        if er.ok:
            vor = km - I._kum(er, basis, real, alter)
            vor0 = (I._kum(e0, basis, real, alter) - I._kum(er0, basis, real, alter)) if (k0 and er0.ok) else None
            st.metric(f"Vorsprung ggü. „{ref_name}“", eur(vor), delta=eur(vor - vor0) if vor0 is not None else None)
            be = E.break_even(er, e, basis, real)
            st.caption("⚖️ " + be["text"])
            fig = go.Figure()
            s, r_ = E.serie_kumuliert(e, basis, real), E.serie_kumuliert(er, basis, real)
            n = min(len(s), len(r_))
            if k0 and er0.ok:
                s0_, r0_ = E.serie_kumuliert(e0, basis, real), E.serie_kumuliert(er0, basis, real)
                n0 = min(len(s0_), len(r0_))
                fig.add_trace(go.Scatter(x=s0_.index[:n0], y=s0_.values[:n0] - r0_.values[:n0], name="vorher",
                                         line=dict(color="#999", dash="dot", width=2)))
            fig.add_trace(go.Scatter(x=s.index[:n], y=s.values[:n] - r_.values[:n], name="jetzt", line=dict(color="#0072B2", width=3)))
            fig.add_hline(y=0, line_color="#888", line_width=1)
            fig.update_layout(height=230, margin=dict(l=0, r=0, t=28, b=0), title=dict(text="Vorsprung über die Zeit (€)", font=dict(size=13)),
                              legend=dict(orientation="h", y=-0.25), yaxis_tickformat=",.0f", hovermode="x unified")
            st.plotly_chart(fig, width="stretch")


def page_ergebnis():
    ui = S()["ui"]
    st.markdown(LIVE_CSS, unsafe_allow_html=True)
    st.header("7 · Ergebnis")
    col_l, col_r = st.columns([1, 2.7], gap="large")
    with col_l:
        mi, kpi_box = live_controls()          # Phase 1: Regler (ändern den Store)
    pr = _proj()
    ergs_all = [rechne(pr.person, pr.annahmen, s) for s in pr.szenarien]
    with col_r:
        for e in ergs_all:
            if not e.ok:
                st.error(f"**{e.szenario.name}:** " + " ".join(e.fehler) + " – bitte Regler links oder „5 · Szenarien“ anpassen.")
    ergs = [e for e in ergs_all if e.ok]
    if len(ergs) < 1:
        with col_r:
            st.info("Kein gültiges Szenario vorhanden.")
        return
    with col_r:
        with st.container(border=True):
            c = st.columns([1.2, 2.2, 1.2, 1.2])
            W(c[0].radio, "Werte in …", ui, "real", "ui", ["Nominal", "Real"], horizontal=True,
              help="Nominal = Euro-Beträge der jeweiligen Zukunftsjahre. Real = in heutiger Kaufkraft (inflationsbereinigt) – für Vergleiche über Jahrzehnte aussagekräftiger.")
            W(c[1].radio, "Was vergleichen?", ui, "basis", "ui", [1, 2, 3], format_func=BASIS_LABEL.get,
              help="1: Nur Rentenzahlungen (abzgl. Ausgleichszahlung). 2: zusätzlich Gehalt in Mehrarbeitsjahren, Nebenjob, Entnahmen. 3: zusätzlich Wertveränderung des Depots.")
            names = [e.szenario.name for e in ergs]
            ui["ref"] = min(ui["ref"], len(names) - 1)
            W(c[2].selectbox, "Referenz", ui, "ref", "ui", list(range(len(names))), format_func=lambda i: names[i],
              help="Gegen dieses Szenario wird der Break-even berechnet (meist die früheste Rente).")
            W(c[3].slider, "Betrachtungsalter", ui, "kum_alter", "ui", 70, 95,
              help="Bis zu welchem Lebensjahr summieren wir? (Durchschnittliche Lebenserwartung mit 65: ca. 20 weitere Jahre.)")
    real, basis, ref, alter = ui["real"] == "Real", ui["basis"], ui["ref"], ui["kum_alter"]
    if kpi_box is not None:
        with kpi_box:
            live_kennzahlen(mi, ergs_all, pr, basis, real, names[ref], alter)   # Phase 2: Wirkung der Regler
    with col_r:
        if basis != 1:
            st.caption("ℹ️ Bei dieser Vergleichsbasis fließt auch Gehalt aus zusätzlichen Arbeitsjahren ein – ein späterer Rentenbeginn wirkt daher oft sofort vorteilhaft.")
        for e in ergs:
            for w in e.warnungen:
                st.warning(f"**{e.szenario.name}:** {w}")
        t1, t2, t3, tw, t4, t5, t6 = st.tabs(["🏁 Überblick", "📈 Verlauf", "⚖️ Break-even", "🧮 Woher der Unterschied?", "🎚 Einflussfaktoren", "🔍 Rechenweg", "📚 Zahlen & Methodik"])


    with t1:
        st.markdown('<div class="fazit">' + re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", "<br>".join(I.fazit(ergs, basis, real, alter))) + "</div>", unsafe_allow_html=True)
        cols = st.columns(min(4, len(ergs)))
        for n, e in enumerate(ergs):
            k = e.kennzahlen
            with cols[n % len(cols)].container(border=True):
                st.markdown(f"<span style='color:{FARBEN[n % len(FARBEN)]}'>●</span> **{e.szenario.name}**", unsafe_allow_html=True)
                st.markdown(f"<span class='pill'>{E.fmt_alter(k['rentenbeginn_alter_m'])}</span>"
                            f"<span class='pill'>{'−%.1f %% Abschlag' % k['abschlag_prozent'] if k['abschlag_prozent'] else KURZ.get(k['rentenart'], k['rentenart'])}</span>",
                            unsafe_allow_html=True)
                st.metric("Netto-Rente / Monat", eur(k["rente_netto_start_real" if real else "rente_netto_start"]),
                          help="Durchschnitt im ersten vollen Rentenjahr, nach KV/PV und Steuer.")
                st.metric(f"Summe bis {alter}", eur(I._kum(e, basis, real, alter)))
        lt = I.fuehrung_tabelle(ergs, basis, real)
        if len(lt):
            st.markdown("**Wer liegt bei welchem Alter vorn?**")
            lt["Vorsprung"] = lt["Vorsprung"].map(eur)
            lt["Alter"] = lt["Alter"].map(lambda a: f"bis {a}")
            st.dataframe(lt.set_index("Alter"), width="stretch")
        with st.expander("Alle Kennzahlen als Tabelle"):
            st.dataframe(E.vergleich_tabelle(ergs, real, basis).set_index("Szenario"), width="stretch")

    with t2:
        st.plotly_chart(chart_monatlich(ergs, basis, real), width="stretch")
        WHY("<b>So liest du das Diagramm:</b> Jede Linie zeigt, wie viel Netto du pro Monat zur Verfügung hast. Der Sprung ist der Rentenbeginn. "
            "Früher Beginn = früher Geld, aber niedriger (Abschlag). Die Steigung kommt von der Rentenanpassung" + (", abzüglich Inflation." if real else ".") )
        if any(e.monat["depot_wert"].iloc[0] > 0 for e in ergs):
            st.plotly_chart(chart_depot(ergs, real), width="stretch")
            WHY("Depotwert über die Zeit. Fällt die Linie, wird Kapital zur Überbrückung verbraucht.")

    with t3:
        st.plotly_chart(chart_differenz(ergs, basis, real, ref), width="stretch")
        WHY("<b>So liest du das Diagramm:</b> Es zeigt, wie viel mehr (oder weniger) Netto ein Szenario <b>im Vergleich zur Referenz</b> bis zu diesem Alter insgesamt erhalten hat. "
            "Unter der Null-Linie liegt es hinter der Referenz, darüber davor. Wo die Linie die Null kreuzt, ist der <b>Break-even</b>: "
            "Der Verzicht auf frühere Rentenjahre hat sich amortisiert.")
        with st.expander("Gesamtverlauf aller Szenarien (kumuliert)"):
            st.plotly_chart(chart_kumuliert(ergs, basis, real, ref), width="stretch")
            WHY("Jede Linie summiert alles, was bis zu diesem Alter netto geflossen ist. Die Linien liegen eng beieinander – die Unterschiede sieht man in der Differenz-Grafik darüber deutlich besser.")
        rows = []
        for e in ergs:
            if e is ergs[ref]:
                continue
            be = E.break_even(ergs[ref], e, basis, real)
            bv = E.break_even_vereinfacht(ergs[ref], e)
            rows.append({"Szenario": e.szenario.name, "Break-even": be["text"], "Faustformel (ohne Dynamik/Steuer)": bv["text"]})
        if rows:
            st.dataframe(pd.DataFrame(rows).set_index("Szenario"), width="stretch")

    with tw:
        st.subheader("Woraus setzt sich der Unterschied zusammen?")
        WHY("Der <b>Wasserfall</b> zerlegt den Unterschied zwischen zwei Szenarien bis zum gewählten Alter in seine Bausteine. "
            "<b>Grün</b> = bringt dem gewählten Szenario mehr Geld, <b>orange</b> = kostet es Geld, <b>blau</b> = Ergebnis (Summe aller Bausteine). "
            "Du siehst z. B. direkt: „Die höhere Bruttorente bringt +80.000 €, aber der fehlende Rentenbezug in den ersten Jahren kostet −60.000 €.“")
        if len(ergs) < 2:
            st.info("Dafür brauchst du mindestens zwei gültige Szenarien.")
        else:
            names_w = [e.szenario.name for e in ergs]
            cand_w = [i for i in range(len(ergs)) if i != ref]
            selw = st.selectbox("Szenario", cand_w, format_func=lambda i: names_w[i], key="w_wf_sel")
            dfw = I.zerlegung(ergs[selw], ergs[ref], basis, real, alter)
            if dfw.empty:
                st.info("Die beiden Szenarien unterscheiden sich bei dieser Einstellung nicht.")
            else:
                st.plotly_chart(chart_wasserfall(dfw, names_w[selw], names_w[ref]), width="stretch")
                tot = dfw["Δ"].sum()
                gross = dfw.loc[dfw["Δ"].abs().idxmax()]
                st.markdown(f"**Ergebnis bis Alter {alter}:** „{names_w[selw]}“ liegt {eur(abs(tot))} "
                            f"{'vor' if tot >= 0 else 'hinter'} „{names_w[ref]}“. Der größte Baustein ist **{gross['Komponente']}** ({eur(gross['Δ'])}).")
                st.caption("Bausteine unter 1 € werden ausgeblendet. Werte nominal bzw. real je nach Einstellung oben.")

    with t4:
        st.subheader("Was beeinflusst das Ergebnis am meisten?")
        WHY("<b>Die Frage:</b> Meine Annahmen sind Schätzungen. Welche davon entscheidet, ob ein Szenario besser ist als die Referenz? "
            "Wir verschieben dazu jede Annahme einzeln nach <b>unten (blau)</b> und nach <b>oben (orange)</b> und messen, "
            "<b>um wie viel Euro sich der Vorsprung dadurch ändert</b>.<br><br>"
            "<b>So liest du es:</b> Ein Balken nach <b>rechts</b> heißt: Das Szenario schneidet dann <i>besser</i> ab als im Basisfall. "
            "Nach <b>links</b> heißt: <i>schlechter</i>. Je länger der Balken, desto wichtiger ist die Annahme. "
            "Fehlt ein Balken, hat die Annahme bei der gewählten Vergleichsbasis keinen Einfluss.")
        if len(ergs) < 2:
            st.info("Dafür brauchst du mindestens zwei gültige Szenarien.")
        else:
            names = [e.szenario.name for e in ergs]
            cand = [i for i in range(len(ergs)) if i != ref]
            sel = st.selectbox("Szenario, dessen Vorsprung analysiert wird", cand, format_func=lambda i: names[i], key="w_einfl_sel")
            idx_all = [x.name for x in pr.szenarien]
            i_s, i_r = idx_all.index(names[sel]), idx_all.index(names[ref])
            df = _einfluss(json.dumps(asdict(pr.person)), json.dumps(asdict(pr.annahmen)),
                           json.dumps([asdict(s) for s in pr.szenarien]), i_s, i_r, basis, real, alter)
            st.caption(f"Basiswert: Vorsprung von „{names[sel]}“ gegenüber „{names[ref]}“ bis Alter {alter}: **{eur(df.attrs['basis_wert'])}**")
            st.plotly_chart(chart_tornado(df[df["Spanne"] >= 1] if (df["Spanne"] >= 1).any() else df, "Wie stark verändert die Annahme den Vorsprung? (€)"), width="stretch")
            top = df.iloc[-1]
            basis_w = df.attrs["basis_wert"]
            st.markdown("**In Worten:**")
            for r in df.iloc[::-1].itertuples():
                if r.Spanne < 1:
                    continue
                def _s(d):
                    return f"{'steigt' if d > 0 else 'sinkt'} der Vorsprung um {eur(abs(d))}"
                st.markdown(f"- **{r.Faktor}** (jetzt {r.aktuell}): bei {r.niedrig} {_s(r._5)}, bei {r.hoch} {_s(r._6)}.")
            ohne = df[df["Spanne"] < 1]["Faktor"].tolist()
            if ohne:
                st.caption("Ohne Einfluss bei dieser Vergleichsbasis: " + ", ".join(ohne))
            WHY(f"<b>Fazit:</b> Am stärksten hängt das Ergebnis von <b>{top['Faktor']}</b> ab (bis ±{eur(top['Spanne'])}). "
                + ("Selbst bei den ungünstigsten Annahmen bleibt der Vorsprung positiv – das Ergebnis ist <b>robust</b>."
                   if (basis_w + df[['Δ niedrig', 'Δ hoch']].min().min()) > 0 else
                   "Der Vorsprung kann bei ungünstigen Annahmen ins Negative kippen – das Ergebnis ist <b>nicht eindeutig</b>."))

    with t5:
        e = ergs[st.selectbox("Szenario", range(len(ergs)), format_func=lambda i: ergs[i].szenario.name, key="w_trace_sz")]
        WHY("Hier siehst du jeden Rechenschritt mit Formel, Zwischenergebnis und Rechtsgrundlage – gruppiert nach Thema und Jahr.")
        gr = e.trace.gruppen()
        auswahl = st.selectbox("Rechenschritte für", gr, key="w_trace_gr")
        df = pd.DataFrame([{"Schritt": s.titel, "Herleitung": s.formel, "Wert": str(s.wert), "Einheit": s.einheit, "Rechtsbezug / Hinweis": s.hinweis}
                           for s in e.trace.fuer(auswahl)])
        st.dataframe(df, width="stretch", hide_index=True)
        with st.expander("Jahresübersicht Steuer & Abzüge"):
            st.dataframe(e.jahr.round(2), width="stretch", hide_index=True)
        with st.expander("Monatsreihe"):
            md = e.monat[["jahr", "monat", "alter", "gehalt_netto", "rente_brutto", "rente_kv", "rente_pv", "steuer_rente", "rente_netto",
                          "hinz_netto", "entnahme_netto", "luecken_kv", "depot_wert"]]
            st.dataframe(md.round(2), width="stretch", hide_index=True)
            st.download_button("Als CSV", e.monat.to_csv(index=False).encode("utf-8"), file_name="monatsreihe.csv", mime="text/csv")

    with t6:
        st.dataframe(pd.DataFrame(C.parameter_tabelle(), columns=["Gruppe", "Parameter", "Wert"]), hide_index=True, width="stretch")
        st.markdown(METHODIK)


METHODIK = """
### Modellannahmen und bekannte Vereinfachungen
* **Besteuerungsanteil:** ab 2023 +0,5 %-Punkte p.a. (2023: 83 %, 2025: 84 %, **2026: 84,5 %**, 100 % ab 2058). Der Rentenfreibetrag wird im
  2. Bezugsjahr festgeschrieben; spätere Erhöhungen sind zu 100 % steuerpflichtig.
* **Steuer:** Einkommensteuer-Veranlagung des Haushalts (Grund-/Splittingtarif 2026, mit Indexierung fortgeschrieben); Steuer der Person per
  Inkrementalmethode (Haushalt − Partner allein). Steuerklassen beeinflussen nur den Lohnsteuerabzug.
* **Ausgleichszahlung (§ 187a):** Preis je EP = Durchschnittsentgelt × Beitragssatz (Näherung). Verbindlich ist die DRV-Auskunft.
* **KV/PV:** KVdR: Rentner trägt 7,3 % + ½ Zusatzbeitrag, PV voll. Freiwillig: Beitrag auf Rente + sonstige Einkünfte, DRV-Zuschuss auf die Rente.
  Depot-Kapitalerträge werden nicht beitragspflichtig modelliert.
* **Hinzuverdienst:** Minijob abgabenfrei; darüber normale AN-Abzüge; EP-Zuschläge (ZF 1,0 vereinfacht) ab 1. Juli des Folgejahres.
  Hinzuverdienstgrenze bei Teilrente wird nicht geprüft.
* **Depot:** Abgeltungsteuer 26,375 % auf Gewinnanteil nach Teilfreistellung, Sparer-Pauschbetrag; Vorabpauschale/Kirchensteuer nicht modelliert.
* **Nicht modelliert:** Arbeitslosengeld, Schwerbehindertenrente, Erwerbsminderung, Betriebsrenten-Details, Hinterbliebenenrenten.
* **Kumulierung:** Basis 1 = Netto-Rente − Ausgleichszahlung + deren Steuerersparnis; Basis 2 = alle Netto-Einkünfte inkl. Entnahmen;
  Basis 3 = Basis 2 + Depot-Wertveränderung. Real = durch Inflation seit Start deflationiert.

Die Parameter in `config.py` sind jährlich zu prüfen.
"""


# ============================================================================
# Hauptprogramm
# ============================================================================
SEITEN = [page_start, page_du, page_rente, page_vers, page_verm, page_szen, page_ann, page_ergebnis]


def main():
    st.set_page_config(page_title="Rentenvergleich", layout="wide", page_icon="📈", initial_sidebar_state="collapsed")
    st.markdown(CSS, unsafe_allow_html=True)
    if "store" not in st.session_state:
        _set_projekt(standard_projekt())
        st.session_state["step"] = 0
    with st.sidebar:
        st.header("Projekt")
        st.download_button("💾 Speichern (JSON)", _proj().to_json(), file_name="rentenprojekt.json", mime="application/json")
        up = st.file_uploader("📂 Laden", type="json")
        if up is not None and st.session_state.get("_geladen") != up.file_id:
            st.session_state["_geladen"] = up.file_id
            try:
                _set_projekt(Projekt.from_json(up.getvalue().decode("utf-8")))
                st.session_state["step"] = 7
                st.rerun()
            except Exception as ex:  # noqa: BLE001
                st.error(f"Datei nicht lesbar: {ex}")
        if st.button("Zurücksetzen (Beispieldaten)"):
            _set_projekt(standard_projekt())
            st.session_state["step"] = 0
            st.rerun()
        st.caption("Beispielwerte sind vorbelegt – ersetze sie durch deine eigenen.")
    stepper()
    SEITEN[st.session_state["step"]]()
    if st.session_state["step"] != 7:
        nav_unten()


if __name__ == "__main__":
    main()
