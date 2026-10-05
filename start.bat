@echo off
cd /d "%~dp0"
where py >nul 2>nul && (set PY=py) || (set PY=python)
if not exist .venv\Scripts\python.exe (
    echo Erste Einrichtung - einmalig, dauert ein paar Minuten ...
    %PY% -m venv .venv || goto :fehler
    .venv\Scripts\python.exe -m pip install -r requirements.txt || goto :fehler
)
.venv\Scripts\python.exe -m streamlit run app.py
goto :ende
:fehler
echo.
echo Fehler bei der Einrichtung. Ist Python 3.10+ installiert (python.org, "Add to PATH" ankreuzen)?
pause
:ende
