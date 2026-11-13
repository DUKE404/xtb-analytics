# XTB Analytics – wskazówki dla Claude Code

Aplikacja do analizy portfela z eksportów xStation (XTB): API we Flasku + mobilna PWA w czystym JS.
Działa na Render (darmowy plan), konta i zaszyfrowany sejf w Postgresie (Neon). Interfejs, komunikaty,
dokumentacja i opisy commitów są **po polsku**.

## Zasady, których nie łamiemy
- Tylko analiza: **żadnych rekomendacji „kup/sprzedaj”** i żadnego logowania do konta XTB.
- Warstwa AI wysyła do Claude wyłącznie zagregowane metryki, bez danych osobowych (`server/ai_payload.py`).
- Hasło użytkownika nie opuszcza telefonu: klucze wyprowadza `web/js/vault.js` (PBKDF2-SHA256 600k + HKDF),
  serwer dostaje tylko klucz logowania i szyfrogramy (AES-256-GCM). Nie dodawaj niczego, co wysyła
  na serwer pliki, nazwy plików albo ustawienia w postaci jawnej.
- Nie wypisuj sekretów (DATABASE_URL, APP_PASSWORD, SECRET_KEY, ANTHROPIC_API_KEY) w logach, odpowiedziach ani commitach.
- Założenia i decyzje projektowe dopisuj do `ASSUMPTIONS.md`; instrukcje dla użytkownika do `README.md`.

## Uruchamianie i testy
- Lokalnie: `./start.sh` (albo `make run` → `python -m server`). Bez `DATABASE_URL` konta trafiają do SQLite
  w `.cache/accounts.sqlite`.
- Testy: `python -m pytest -p no:logging -q` (lub `make test`). Testy przeglądarkowe w `tests/test_ui.py`
  używają Playwright (`playwright install chromium`); `UI_SHOTS=/tmp/shots` zapisuje zrzuty ekranu.
- Testy kont na prawdziwym Postgresie: `TEST_DATABASE_URL=postgresql://... python -m pytest tests/test_auth.py`
  (bez tej zmiennej tylko SQLite).
- Przed commitem cały zestaw testów ma przechodzić. Poprawka błędu = test, który na starym kodzie nie przechodzi.

## Architektura
- `core/` – silnik obliczeń (wyniki, ryzyko, CFD, podatki, symulacje, atrybucja, wycena spółek). Czysty Python/pandas, bez Flaska.
- `data/` – parser eksportu xStation (`xtb_parser.py`), ładowanie i przeliczanie walut (`loader.py`),
  notowania i dane spółek (`providers.py`), dane przykładowe (`synthetic.py`).
- `server/app.py` – API: sesje analizy, konta (`accounts.py`, `db.py`), sejf, nagłówki bezpieczeństwa (CSP bez inline JS).
- `server/screens/` – ekrany sterowane z serwera: każdy zwraca listę bloków z `server/blocks.py`
  (`statement`, `kpis`, `chart`, `table`, `feed`…). Nawigacja i zakładki: `server/screens/__init__.py`.
- `web/` – PWA bez frameworka i bez bundlera (moduły ES): `app.js` (start, nawigacja, pasek okresu),
  `api.js` (sesja, logowanie), `store.js` (ustawienia i pliki w sejfie), `blocks.js` (renderowanie bloków),
  `charts.js` (własne wykresy SVG, m.in. wykres liniowy z przybliżaniem), `client.js` (ekrany importu,
  ustawień, logowania). Po zmianie plików z `web/` podbij `VERSION` w `web/sw.js`.
- Kwoty pokazywane w walucie wybranej przez użytkownika (PLN/USD/EUR); okres analiz (`since`/`until`)
  ustawiany globalnie przez `/api/session/<sid>/view`.

## Wdrożenie
- Render: usługa `xtb-analytics` (Frankfurt), auto-deploy z gałęzi `main`, konfiguracja w `render.yaml`.
  Zmienne środowiskowe ustawia się w panelu Render, nie w repozytorium.
- Darmowa instancja usypia się po bezczynności; start trwa do minuty – klient czeka na `/api/health`
  i ponawia chwilowe błędy zamiast przechodzić na dane przykładowe.
