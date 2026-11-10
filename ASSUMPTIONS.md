# Założenia

Lista decyzji podjętych tam, gdzie specyfikacja zostawiała pole do interpretacji. Każde z nich da się
zmienić w kodzie; najważniejsze są też wystawione w UI (panel boczny, tabela mapowań).

## Środowisko budowy

- Aplikacja powstała w środowisku bez internetu i bez możliwości instalacji pakietów. Moduły `core/` i `data/`
  są przetestowane pytestem. Interfejs Streamlit był tam sprawdzany testem dymnym na atrapach bibliotek
  (`tests/fakes/`); u użytkownika `make test` uruchamia prawdziwy test `streamlit.testing.AppTest`.
- Pobieranie na żywo (yfinance, Stooq, NBP, biblioteka K. Frencha) nie mogło być tam wywołane. Przetestowane
  są parsery formatów odpowiedzi i logika cache/fallbacku, nie same połączenia.
- `riskfolio-lib` zastąpiono własną implementacją (min-variance, max Sharpe, risk parity, HRP) - specyfikacja
  to dopuszcza, a biblioteka ciągnie ciężkie zależności (cvxpy), które psują instalację „jedną komendą”.
- HMM do reżimów rynkowych zaimplementowano w numpy (bez `hmmlearn`), regresję OLS także (bez `statsmodels`).

## Etap 1 - import i rekonstrukcja portfela

- **Format eksportu.** Parser rozpoznaje arkusze po nazwie i po nagłówkach (PL/EN), szuka wiersza nagłówków
  w pierwszych 40 wierszach, usuwa wiersze „Total/Suma”. Sufiks `_9` w symbolu (akcje rzeczywiste) jest
  obcinany, `_4` (CFD na akcje) zostaje i oznacza CFD.
- **Waluta konta.** Zakładam konto w PLN: kwoty w operacjach gotówkowych, „Purchase value”, „Sale value”
  i „Gross P/L” są w PLN, ceny w walucie instrumentu. Konto w EUR/USD jest obsłużone w podstawowym zakresie:
  kwoty przeliczane na PLN po kursie NBP z dnia operacji.
- **Dane przykładowe** są w pełni syntetyczne (`data/sample_generator.py`), a ceny w trybie demo pochodzą
  z modelu czynnikowego (`data/synthetic.py`), nie z rynku. W zachowaniu przykładowego tradera celowo zaszyto
  dwa wzorce (większe pozycje CFD po stracie, szybsza realizacja zysków niż strat), żeby było co wykrywać.
- **Kalendarz.** Dni robocze pon-pt. Operacje z weekendu trafiają na najbliższy dzień roboczy. Ceny w dni
  świąteczne danej giełdy = ostatnie znane zamknięcie.
- **Stan posiadania** liczony na koniec dnia: pozycja otwarta w dniu D jest w portfelu od D, zamknięta w D już
  nie. Pozycje intraday nie pojawiają się w stanie - ich wynik jest w operacji „close trade”.
- **Wycena.** Akcje/ETF: ilość × cena zamknięcia × kurs średni NBP. CFD: w wartości portfela jest tylko
  niezrealizowany wynik `loty × mnożnik × (cena − cena otwarcia) × kurs`; depozyt zostaje w gotówce.
  Ekspozycja CFD = nominał ze znakiem.
- **Gotówka** = skumulowana suma operacji gotówkowych z raportu. Wymaga to pełnej historii operacji.
- **Przepływy zewnętrzne** = wpłaty, wypłaty, transfery. Dywidendy, odsetki, podatki, swapy i prowizje to wynik.
  Nierozpoznany typ operacji jest traktowany jako wynik (i wypisany w uwagach).
- **Brak notowań** dla instrumentu: pozycja wyceniana po cenie otwarcia (płasko) + ostrzeżenie, żeby jedna
  zła nazwa tickera nie psuła całej krzywej kapitału.
- **Mnożniki CFD** (wartość punktu na lot) i stawki depozytu pochodzą z tabeli w `data/mapping.py` wg
  specyfikacji instrumentów XTB znanej autorowi; są edytowalne w UI. Sprawdź je dla swoich instrumentów.
- **Ceny z Yahoo** są skorygowane o splity, ale nie o dywidendy (dywidendy przychodzą z operacji gotówkowych).
  Akcje z LSE (`.UK`) mają domyślnie mnożnik 0,01 (pensy → funty); znane ETF-y notowane w USD mają 1.
- **Cache**: SQLite w `.cache/market.sqlite`; dane odświeżane, gdy brakuje zakresu albo są starsze niż 12 h.

## Etap 2 - performance

- **TWR** liczony dziennie z przepływami na początku dnia: `r = V_t / (V_{t-1} + F_t) − 1`, potem łańcuchowany.
- **MWR/XIRR**: konwencja ACT/365 jak w Excelu; wpłata ujemna, wypłata i wartość końcowa dodatnie.
  Dla okresu krótszego niż cała historia wartość portfela na początku okresu jest traktowana jak wpłata.
- **Annualizacja**: 252 dni sesyjne. Zmienność i Sharpe z odchylenia standardowego próby (ddof=1).
- **Stopa wolna od ryzyka**: domyślnie 4,0% rocznie, ustawiana w panelu bocznym (Sharpe, Sortino, alfa).
- **Sortino**: odchylenie w dół liczone względem stopy wolnej od ryzyka, po wszystkich dniach.
- **Obsunięcie** liczone na indeksie TWR (nie na wartości w PLN), więc wpłaty go nie maskują; poziom startowy
  też jest szczytem. Czasy trwania w dniach kalendarzowych.
- **Benchmarki**: WIG20, WIG, S&P 500 i ETF na MSCI World (URTH) przeliczone do PLN kursem NBP. WIG jest
  indeksem dochodowym, WIG20 i S&P 500 cenowymi - portfel z dywidendami ma nad nimi lekką przewagę z definicji.
- **Beta/alfa/TE/IR** z dziennych zwrotów względem benchmarku wybranego w panelu bocznym (domyślnie MSCI World).
  Giełdy w USA zamykają się po GPW, więc dzienna beta portfela mieszanego jest lekko zaniżona.
- **Atrybucja**: dzienny wynik instrumentu = zmiana wyceny + jego operacje gotówkowe. Rozkład na efekt cen
  (ilość z wczoraj × zmiana ceny × kurs z wczoraj), efekt walutowy (wartość w walucie × zmiana kursu, z członem
  mieszanym), dywidendy netto i resztę (koszty, swapy, intraday, różnica cena transakcji/zamknięcie). Wkłady
  w TWR łańcuchowane tak, by sumowały się dokładnie do TWR.
