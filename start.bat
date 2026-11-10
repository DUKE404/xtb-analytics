@echo off
REM Uruchomienie jedna komenda w Windows: tworzy venv, instaluje zaleznosci, odpala Streamlit.
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
  echo Nie znaleziono Pythona. Zainstaluj Python 3.11+ z https://www.python.org/downloads/ ^(zaznacz "Add Python to PATH"^).
  pause
  exit /b 1
)

if not exist .venv\.ok (
  echo ^>^> Tworze srodowisko i instaluje zaleznosci ^(tylko za pierwszym razem, 2-5 minut^)...
  python -m venv .venv
  .venv\Scripts\python -m pip install --upgrade pip
  .venv\Scripts\python -m pip install -r requirements.txt
  if errorlevel 1 (
    echo Instalacja nie powiodla sie.
    pause
    exit /b 1
  )
  echo ok > .venv\.ok
)

echo ^>^> Startuje aplikacje. Przegladarka otworzy sie pod adresem http://localhost:8501
.venv\Scripts\streamlit run app\main.py
pause
