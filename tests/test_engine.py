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
    assert len(I.vorlagen(1967)) == 6


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
