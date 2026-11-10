#!/usr/bin/env bash
# Uruchomienie jedną komendą: tworzy venv, instaluje zależności, odpala Streamlit.
set -euo pipefail
cd "$(dirname "$0")"

PY="${PYTHON:-python3}"
if ! command -v "$PY" >/dev/null 2>&1; then
  echo "Nie znaleziono Pythona 3. Zainstaluj Python 3.11+ ze strony https://www.python.org/downloads/" >&2
  exit 1
fi

if [ ! -f .venv/.ok ]; then
  echo ">> Tworzę środowisko i instaluję zależności (tylko za pierwszym razem, 2-5 minut)..."
  "$PY" -m venv .venv
  .venv/bin/pip install --upgrade pip >/dev/null
  .venv/bin/pip install -r requirements.txt
  touch .venv/.ok
fi

echo ">> Startuję aplikację. Przeglądarka otworzy się pod adresem http://localhost:8501"
exec .venv/bin/streamlit run app/main.py
