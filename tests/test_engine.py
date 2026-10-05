import math
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import config as C
import engine as E
from models import Person, Annahmen, Szenario, standard_projekt


def test_tarif_stetig_und_grundfreibetrag():
    assert E.einkommensteuer(12_348, False) == 0
    for grenze in (C.TARIF_ZONE2_BIS, C.TARIF_ZONE3_BIS, C.TARIF_ZONE4_BIS):
        assert abs(E._tarif_basis(grenze) - E._tarif_basis(grenze + 1e-6)) < 0.1   # Rundung der Gesetzesparameter
    assert E.einkommensteuer(100_000, False) > E.einkommensteuer(60_000, False)


def test_splitting_verdoppelt():
    assert E.einkommensteuer(80_000, True) == 2 * E.einkommensteuer(40_000, False)


def test_soli_freigrenze():
    assert E.solidaritaetszuschlag(20_000, False) == 0
    assert 0 < E.solidaritaetszuschlag(25_000, False) < 0.055 * 25_000


def test_besteuerungsanteil():
    assert C.besteuerungsanteil(2005) == 50
    assert C.besteuerungsanteil(2020) == 80
    assert C.besteuerungsanteil(2023) == 83
    assert C.besteuerungsanteil(2026) == 84.5
    assert C.besteuerungsanteil(2058) == 100 and C.besteuerungsanteil(2070) == 100


def test_altersgrenzen():
    assert C.regelaltersgrenze_monate(1958) == 66 * 12
    assert C.regelaltersgrenze_monate(1960) == 66 * 12 + 4
    assert C.regelaltersgrenze_monate(1964) == 67 * 12
    assert C.abschlagsfrei_besonders_langjaehrig_monate(1960) == 64 * 12 + 4
    assert C.abschlagsfrei_besonders_langjaehrig_monate(1964) == 65 * 12


def test_abschlag_max_14_4():
    a = E.rentenanspruch(1967, 63 * 12, 40, 40)
    assert a.ok and a.abschlag_monate == 48 and abs(a.zugangsfaktor - 0.856) < 1e-9
    a = E.rentenanspruch(1967, 65 * 12, 40, 45)
    assert a.ok and a.zugangsfaktor == 1 and "besonders" in a.art
    a = E.rentenanspruch(1967, 65 * 12, 40, 44)       # 45 Jahre nicht erreicht
    assert a.ok and a.abschlag_monate == 24
    assert not E.rentenanspruch(1967, 62 * 12, 40, 40).ok
    assert not E.rentenanspruch(1967, 64 * 12, 30, 30).ok
    a = E.rentenanspruch(1967, 68 * 12, 40, 40)
    assert abs(a.zugangsfaktor - 1.06) < 1e-9


def test_pv_kinder():
    p = Person(kinder_geburtsjahre=[])
    assert abs(E.pv_saetze(p, 2030)["voll"] - 0.042) < 1e-9
    p = Person(kinder_geburtsjahre=[2010, 2012, 2015])
    assert abs(E.pv_saetze(p, 2030)["voll"] - (0.036 - 0.005)) < 1e-9
    p = Person(kinder_geburtsjahre=[1990, 1992])
    assert abs(E.pv_saetze(p, 2030)["voll"] - 0.036) < 1e-9


def _run(**kw):
    pr = standard_projekt()
    pr.annahmen.start = "2026-10"
    for k, v in kw.pop("person", {}).items():
        setattr(pr.person, k, v)
    for k, v in kw.pop("ann", {}).items():
        setattr(pr.annahmen, k, v)
    return E.simuliere(pr.person, pr.annahmen, Szenario(**kw))


def test_rente_hoehe_formel():
    r = _run(erwerbsende_alter_m=67 * 12, rentenbeginn_alter_m=67 * 12)
    k = r.kennzahlen
    p = standard_projekt().person
    months = (E.midx(1967, 5) + 67 * 12 + 1) - E.midx(2026, 10)
    ep = p.ep_aktuell + p.ep_pro_jahr * months / 12
    rb_idx = E.midx(1967, 5) + 67 * 12 + 1
    assert abs(k["rente_brutto_start"] - ep * E.rentenwert(rb_idx, Annahmen())) < 0.01


def test_rentenfreibetrag_fix_und_dynamik_voll_steuerpflichtig():
    r = _run(erwerbsende_alter_m=67 * 12, rentenbeginn_alter_m=67 * 12,
             person={"sonstige_einkuenfte_jahr": 20_000})
    j = r.jahr.set_index("jahr")
    rb = j[j.rente_brutto > 0].index[0]
    f = j.loc[rb + 1, "rentenfreibetrag"]
    assert f == math.ceil((1 - C.besteuerungsanteil(rb) / 100) * j.loc[rb + 1, "rente_brutto"])
    assert j.loc[rb + 3, "rentenfreibetrag"] == f
    # Zuwachs der Rente = Zuwachs der steuerpflichtigen Rente (100 %)
    d_brutto = j.loc[rb + 3, "rente_brutto"] - j.loc[rb + 2, "rente_brutto"]
    d_stpfl = j.loc[rb + 3, "rente_stpfl"] - j.loc[rb + 2, "rente_stpfl"]
    assert abs(d_brutto - d_stpfl) < 0.01


def test_ausgleichszahlung_hebt_rente_und_spart_steuer():
    a = _run(erwerbsende_alter_m=63 * 12, rentenbeginn_alter_m=63 * 12)
    b = _run(erwerbsende_alter_m=63 * 12, rentenbeginn_alter_m=63 * 12, ausgleich_modus="voll", ausgleich_jahre=3)
    ep = standard_projekt().person
    assert b.kennzahlen["rente_brutto_start"] > a.kennzahlen["rente_brutto_start"]
    # voller Ausgleich => wie ohne Abschlag
    ohne = a.kennzahlen["rente_brutto_start"] / 0.856
    assert abs(b.kennzahlen["rente_brutto_start"] - ohne) < 0.01
    assert b.kennzahlen["ausgleich_summe"] > 0 and b.kennzahlen["steuerersparnis_ausgleich"] > 0
    assert b.kennzahlen["steuerersparnis_ausgleich"] < b.kennzahlen["ausgleich_summe"]


def test_unmoeglicher_rentenbeginn():
    r = _run(erwerbsende_alter_m=62 * 12, rentenbeginn_alter_m=62 * 12)
    assert not r.ok and r.fehler


def test_depot_bruecke_und_luecke():
    r = _run(erwerbsende_alter_m=62 * 12, rentenbeginn_alter_m=67 * 12, wunsch_netto_monat=2500,
             person={"depot_start": 20_000})
    assert r.monat["depot_luecke"].sum() > 0 and any("Depot reicht" in w for w in r.warnungen)
    r2 = _run(erwerbsende_alter_m=62 * 12, rentenbeginn_alter_m=67 * 12, wunsch_netto_monat=2500,
              person={"depot_start": 400_000})
    assert r2.monat["depot_luecke"].sum() < 1e-6
    assert (r2.monat["entnahme_brutto"] >= r2.monat["entnahme_netto"]).all()


def test_break_even_konsistent():
    a = _run(erwerbsende_alter_m=63 * 12, rentenbeginn_alter_m=63 * 12)
    c = _run(erwerbsende_alter_m=67 * 12, rentenbeginn_alter_m=67 * 12)
    be = E.break_even(a, c, 1, False)
    assert be["status"] == "ok" and 67 < be["alter"] < 95
    sa, sc = E.serie_kumuliert(a, 1, False), E.serie_kumuliert(c, 1, False)
    # kurz vor Break-even liegt c unter a, danach darüber
    import numpy as np
    i_vor = np.searchsorted(sa.index.values, be["alter"] - 0.2)
    i_nach = np.searchsorted(sa.index.values, be["alter"] + 0.2)
    assert sc.iloc[i_vor] < sa.iloc[i_vor] and sc.iloc[i_nach] > sa.iloc[i_nach]


def test_real_kleiner_als_nominal():
    a = _run(erwerbsende_alter_m=63 * 12, rentenbeginn_alter_m=63 * 12)
    k = a.kennzahlen
    assert k["kum1_real_85"] < k["kum1_nom_85"]


def test_partner_splitting_senkt_steuer():
    alleine = _run(erwerbsende_alter_m=67 * 12, rentenbeginn_alter_m=67 * 12, person={"sonstige_einkuenfte_jahr": 30_000})
    paar = _run(erwerbsende_alter_m=67 * 12, rentenbeginn_alter_m=67 * 12,
                person={"sonstige_einkuenfte_jahr": 30_000, "steuerklasse": "4", "partner_einkuenfte_jahr": 0.0})
    assert paar.jahr["steuer_person"].sum() < alleine.jahr["steuer_person"].sum()


def test_kvdr_vs_freiwillig():
    a = _run(erwerbsende_alter_m=67 * 12, rentenbeginn_alter_m=67 * 12, person={"gkv_anteil_zweite_haelfte": 95, "sonstige_einkuenfte_jahr": 24_000})
    b = _run(erwerbsende_alter_m=67 * 12, rentenbeginn_alter_m=67 * 12, person={"gkv_anteil_zweite_haelfte": 50, "sonstige_einkuenfte_jahr": 24_000})
    assert b.kennzahlen["rente_netto_start"] < a.kennzahlen["rente_netto_start"]


def test_insights_fazit_und_einfluss():
    import insights as I
    pr = standard_projekt(); pr.annahmen.start = "2026-10"
    ergs = [E.simuliere(pr.person, pr.annahmen, s) for s in pr.szenarien]
    assert I.fazit(ergs, 1, False, 85)
    df = I.einfluss(pr.person, pr.annahmen, pr.szenarien, 2, 0, 1, False, 85)
    assert len(df) >= 6 and "basis_wert" in df.attrs
    assert len(I.vorlagen(pr.person, pr.annahmen)) == 6


def test_zerlegung_summe_gleich_differenz():
    import insights as I
    pr = standard_projekt(); pr.annahmen.start = "2026-10"
    ergs = [E.simuliere(pr.person, pr.annahmen, s) for s in pr.szenarien]
    for basis in (1, 2, 3):
        for real in (False, True):
            for i in (1, 2, 3, 4):
                df = I.zerlegung(ergs[i], ergs[0], basis, real, 85)
                diff = I._kum(ergs[i], basis, real, 85) - I._kum(ergs[0], basis, real, 85)
                assert abs(df["Δ"].sum() - diff) < 5, (basis, real, i, df["Δ"].sum(), diff)


def test_auto_name_folgt_den_einstellungen():
    import insights as I
    pr = standard_projekt(); pr.annahmen.start = "2026-10"
    n63 = I.auto_name(Szenario(erwerbsende_alter_m=63 * 12, rentenbeginn_alter_m=63 * 12), pr.person, pr.annahmen)
    n65 = I.auto_name(Szenario(erwerbsende_alter_m=65 * 12, rentenbeginn_alter_m=65 * 12), pr.person, pr.annahmen)
    assert n63 == "Rente mit 63 (Abschlag 14,4 %)"
    assert n65 == "Rente mit 65 (abschlagsfrei)"
    assert "Aufhören mit 62" in I.auto_name(Szenario(erwerbsende_alter_m=62 * 12, rentenbeginn_alter_m=67 * 12), pr.person, pr.annahmen)


def test_rentenauskunft_ableitungen_und_fahrplan():
    import insights as I
    from dataclasses import replace
    pr = standard_projekt(); pr.annahmen.start = "2026-10"
    p = pr.person
    # Monatsrente statt EP
    p2 = I.leite_ab(replace(p, ep_modus="rente", anwartschaft_eur=1500.0, auskunft_rentenwert=40.0), pr.annahmen)
    assert abs(p2.ep_aktuell - 37.5) < 1e-9
    # Hochrechnung: zurückgerechnete EP/Jahr reproduzieren die Auskunft
    p3 = I.leite_ab(replace(p, ep_modus="ep", ep_aktuell=30.0, fortgang_modus="hochrechnung",
                            hochrechnung_eur=2000.0, hochrechnung_alter_m=67 * 12, auskunft_rentenwert=40.0), pr.annahmen)
    mon = E.midx(1967, 5) + 67 * 12 + 1 - E.midx(2026, 10)
    assert abs(p3.ep_aktuell + p3.ep_pro_jahr * mon / 12 - 2000 / 40.0) < 1e-6
    # später Berufsstart verschiebt den frühesten Rentenbeginn
    frueh = I.fahrplan(I.leite_ab(replace(p, wz_modus="schaetzung", berufsstart="1988-09-01"), pr.annahmen), pr.annahmen)
    spaet = I.fahrplan(I.leite_ab(replace(p, wz_modus="schaetzung", berufsstart="2000-09-01", schul_monate=60), pr.annahmen), pr.annahmen)
    assert frueh["langjaehrig"] == 63 * 12 and spaet["langjaehrig"] > frueh["langjaehrig"]
    assert spaet["abschlagsfrei"] > frueh["abschlagsfrei"]
    # Schul-/Studienzeit zählt nur für 35, nicht für 45 Jahre
    a = I.leite_ab(replace(p, wz_modus="schaetzung", berufsstart="2000-09-01", schul_monate=60), pr.annahmen)
    assert abs((a.wartezeit_jahre_35 - a.wartezeit_jahre_45) - 5.0) < 1e-9


# ---------------------------------------------------------------------------
# Netto-Kette: unabhängige Handrechnung und Plausibilitätsprüfungen
# ---------------------------------------------------------------------------
def _rentner(**person):
    from dataclasses import replace
    pr = standard_projekt(); pr.annahmen.start = "2026-10"
    p = replace(pr.person, depot_start=0, ep_aktuell=44.0, ep_pro_jahr=0.0, geburtsdatum="1961-03-10",
                wartezeit_jahre_35=45, wartezeit_jahre_45=45, kinder_geburtsjahre=[1990], partner_wachstum=0.0, **person)
    a = replace(pr.annahmen, tarif_indexierung=0.0, rentensteigerung=0.0, inflation=0.0)
    return E.simuliere(p, a, Szenario(erwerbsende_alter_m=66 * 12 + 8, rentenbeginn_alter_m=66 * 12 + 8)), p


def test_handrechnung_single_rentner():
    r, _ = _rentner()
    # Rentenbeginn 12/2027, ZF 1,01 (2 Monate Aufschub): 44 EP × 42,52 € × 1,01
    brutto = 44 * 42.52 * 1.01
    assert abs(brutto - 1889.59) < 0.01
    jahr = 12 * brutto
    freib = math.ceil(0.15 * jahr)                                   # Besteuerungsanteil 2027 = 85 %
    kv, pv = 12 * brutto * (0.073 + 0.029 / 2), 12 * brutto * 0.036
    zve = math.floor(jahr - freib - 102 - kv - pv - 36)
    y = (zve - 12_348) / 10_000
    est = math.floor((914.51 * y + 1400) * y)
    erwartet = brutto - (kv + pv + est) / 12
    row = r.monat[r.monat.jahr == 2029].iloc[0]
    assert abs(row["rente_netto"] - erwartet) < 0.01, (row["rente_netto"], erwartet)


def test_zusammenveranlagung_zerlegung_der_haushaltssteuer():
    for partner in (0, 15_000, 60_000):
        r, _ = _rentner(steuerklasse="4", partner_einkuenfte_jahr=float(partner), partner_vorsorge_jahr=partner * 0.1)
        j = r.jahr.set_index("jahr").loc[2029]
        # Zusatzsteuer + Steuer des Partners allein = Steuer des Haushalts
        assert abs(j["steuer_person"] + j["est_basis_partner"] - (j["est_haushalt"] + j["soli"] * 0 + j["kist"] * 0) - (j["soli"] + j["kist"])) < 1.0
        assert j["steuer_person"] >= 0
        # Splitting senkt die Gesamtlast gegenüber getrennter Besteuerung beider Partner
        einzel = E.einkommensteuer(j["zve_person"] - 36, False) + E.einkommensteuer(max(0, j["partner_zve"] - 36), False)
        assert j["est_haushalt"] <= einzel + 1


def test_anteilig_und_marginal_summieren_sich_plausibel():
    r, _ = _rentner(steuerklasse="4", partner_einkuenfte_jahr=54_000.0, partner_vorsorge_jahr=6_000.0)
    j = r.jahr.set_index("jahr").loc[2029]
    assert j["steuer_anteilig"] < j["steuer_person"]          # Marginal belastet den Zweitverdiener stärker
    assert j["steuer_anteilig"] > 0


def test_betriebsrente_kvdr_beitragspflichtig():
    a, _ = _rentner()
    b, _ = _rentner(betriebsrente_monat=400.0, betriebsrente_wachstum=0.0)
    ra, rb = a.monat[a.monat.jahr == 2029].iloc[0], b.monat[b.monat.jahr == 2029].iloc[0]
    lf = E.lohn_index(2029, Annahmen())      # Freibetrag wächst mit der Bezugsgröße
    assert abs(rb["betr_kv"] - (400 - 197.75 * lf) * (0.146 + 0.029)) < 0.01
    assert abs(rb["betr_pv"] - 400 * 0.036) < 0.01
    assert abs(rb["rente_kv"] - ra["rente_kv"]) < 1e-9                     # gesetzliche Rente unverändert
    assert 150 < rb["betr_netto"] < 400 - rb["betr_kv"] - rb["betr_pv"]    # nach Steuer weniger als nach SV
    assert abs(rb["renten_netto"] - (rb["rente_netto"] + rb["betr_netto"])) < 1e-9


def test_brutto_netto_und_partner_helfer():
    import insights as I
    r, p = _rentner()
    bn = I.brutto_netto(r)
    assert abs(bn["brutto"] - bn["kv"] - bn["pv"] - bn["steuer"] - bn["netto"]) < 0.01
    pr = standard_projekt(); pr.annahmen.start = "2026-10"
    g = I.partner_aus_brutto("gehalt", 40_000, pr.person, pr.annahmen)
    assert g["einkuenfte"] == 40_000 - 1_230 and 5_000 < g["vorsorge"] < 9_000
    rn = I.partner_aus_brutto("rente", 20_000, pr.person, pr.annahmen, rentenbeginn_jahr=2020)
    assert rn["einkuenfte"] == 20_000 - math.ceil(0.2 * 20_000) - 102
