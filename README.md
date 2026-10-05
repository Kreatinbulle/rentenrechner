# Renten- & Früheintritts-Szenariorechner

Interaktives Entscheidungstool (Streamlit + Plotly) für den Vergleich von Rentenbeginn-Szenarien nach deutschem Recht:
Abschläge (0,3 %/Monat, max. 14,4 %), abschlagsfreie Rente nach 45 Jahren, Ausgleichszahlung § 187a SGB VI,
Teilrente, Hinzuverdienst, Überbrückung per Depot, KVdR/freiwillige KV, PV-Zuschläge/-Abschläge,
Kohortenbesteuerung (Wachstumschancengesetz, fester Rentenfreibetrag, Dynamisierung 100 % steuerpflichtig),
Splittingtarif, nominal und real (Kaufkraft), Break-even-Analyse.

## Start
```
pip install -r requirements.txt
streamlit run app.py        # oder: streamlit run gui.py
pytest -q tests
```

## Architektur
| Datei | Aufgabe |
|---|---|
| `config.py` | gesetzliche Stellschrauben (Rentenwert, BBG, KV/PV, Tarif, Altersgrenzen, Besteuerungsanteil) – jährlich prüfen |
| `models.py` | Eingabe-Dataclasses (Person, Annahmen, Szenario), JSON-Speichern/Laden |
| `engine.py` | Rechenkern: Rentenanspruch, § 187a, Monatssimulation, Jahressteuer, Depot, Kumulierung, Break-even |
| `tracer.py` | protokolliert jeden Rechenschritt (Formel, Wert, Rechtsbezug) → Tab „Rechenweg“ |
| `gui.py` / `app.py` | Streamlit-Dashboard |

Modellgrenzen und Annahmen: siehe Tab „Methodik & Grenzen“ bzw. `gui.METHODIK`.
Keine Rechts-, Steuer- oder Anlageberatung; verbindlich sind Rentenauskunft und Steuerberater.
