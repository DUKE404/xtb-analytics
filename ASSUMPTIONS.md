# Założenia

Lista decyzji podjętych tam, gdzie specyfikacja zostawiała pole do interpretacji. Każde z nich da się
zmienić w kodzie; najważniejsze są też wystawione w UI (panel boczny, tabela mapowań).

## Zmiana formy w trakcie: aplikacja mobilna (PWA)

- W trakcie budowy zamówienie zmieniło się z aplikacji na komputer (Streamlit) na **aplikację mobilną**.
  Wybrany wariant: PWA z interfejsem pisanym od zera pod telefon + obliczenia w Pythonie na serwerze
  w darmowej chmurze. Silnik `core/` i `data/` został bez zmian; interfejs Streamlit usunięto.
- **Architektura**: `server/` (Flask) wystawia API i serwuje `web/` (HTML/CSS/JS bez etapu budowania i bez
  zewnętrznych bibliotek - wykresy to własny SVG). Ekrany są składane na serwerze z klocków (`server/blocks.py`),
  więc nowy ekran to funkcja w Pythonie, a cała logika jest testowalna pytestem.
- **Dane użytkownika**: eksport z XTB jest zapisany w telefonie (IndexedDB). Na serwer trafia tylko na czas
  obliczeń i żyje w pamięci procesu (maks. 6 sesji, 3 h bezczynności). Gdy serwer zgubi sesję (uśpienie darmowej
  instancji), telefon odtwarza ją sam z zapisanego pliku.
- **Dostęp**: adres w chmurze jest publiczny, więc aplikacja obsługuje hasło (`APP_PASSWORD`). Bez tej zmiennej
  jest otwarta - tak jest wygodnie lokalnie, ale w chmurze hasło trzeba ustawić.
- **Nawigacja**: dolny pasek ma najwyżej 5 pozycji, więc 8 działów ze specyfikacji rozłożono na Przegląd,
  Wyniki, Ryzyko, Symulacje i „Więcej” (Trader, Podatki, AI, Dane i import, Ustawienia). Długie działy mają
  pod-zakładki u góry.
- **Tooltipy**: na telefonie nie ma najechania kursorem, więc objaśnienie „co to znaczy” rozwija się po
  dotknięciu wiersza z liczbą.
- **Kolory**: zysk/plus jest niebieski, strata/minus czerwona (zamiast zielony/czerwony) - para czytelna także
  dla osób z zaburzeniami widzenia barw. Paleta serii sprawdzona walidatorem na tle aplikacji.
- **Krój pisma** nagłówków i zdań to podzbiór znaków kroju Lora (licencja SIL OFL 1.1), dołączony pod zmienioną
  nazwą „Ledger Serif”, jak wymaga licencja dla wersji zmodyfikowanych. Liczby i interfejs: krój systemowy.

## Środowisko budowy

- Aplikacja powstała w środowisku bez internetu i bez możliwości instalacji pakietów. Silnik i API są
  przetestowane pytestem, a interfejs mobilny - w prawdziwej przeglądarce (Chrome bez okna, ekran 390×844,
  `tests/test_ui.py`), łącznie z wgrywaniem pliku i edycją tickerów.
- Pobieranie na żywo (yfinance, Stooq, NBP, biblioteka K. Frencha) nie mogło być tam wywołane. Przetestowane
  są parsery formatów odpowiedzi i logika cache/fallbacku, nie same połączenia. Serwer produkcyjny (gunicorn)
  i wdrożenie na Render też nie były tam uruchamiane.
- `riskfolio-lib` zastąpiono własną implementacją (min-variance, max Sharpe, risk parity, HRP) - specyfikacja
  to dopuszcza, a biblioteka ciągnie ciężkie zależności (cvxpy), które nie mieszczą się w darmowej instancji.
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

## Etap 3 - ryzyko

- **Podejście**: dzisiejsze ekspozycje w PLN × historyczne dzienne zwroty instrumentów widziane z PLN
  (ostatnie 504 sesje, także sprzed zakupu). Dla CFD czynnikiem ryzyka jest sama cena instrumentu - nominał
  CFD nie niesie ryzyka walutowego, tylko jego wynik.
- **VaR/CVaR** podawane jako strata dodatnia. Historyczny: kwantyl empiryczny; dla 10 dni nakładające się
  okna 10-dniowe (przy krótkiej historii skalowanie √10). Parametryczny: rozkład normalny, średnia = 0
  (ostrożnie). Monte Carlo: 20 000 losowań skorelowanych zwrotów wszystkich pozycji; gdy kurtoza historii jest
  dodatnia, z wielowymiarowego rozkładu t-Studenta o stopniach swobody dopasowanych metodą momentów.
- **Efektywna liczba zakładów**: definicja Meucciego (entropia rozkładu wariancji po składowych głównych).
  Przy portfelu samych akcji wychodzi nisko, bo dominuje wspólny czynnik rynkowy - to cecha miary, nie błąd.
- **Koncentracja** (top 5, HHI) liczona po wartości bezwzględnej ekspozycji, bez gotówki.
- **Płynność**: dni do zamknięcia = ilość / (20% średniego wolumenu z 30 sesji). Dla CFD nie liczona.
- **Czynniki Fama-French 5 + momentum**: zwroty portfela przeliczone na USD, domyślnie dane tygodniowe
  (łagodzą różnicę godzin sesji PL/USA). Czynniki są amerykańskie - dla spółek z GPW to przybliżenie.
- **CFD**: zabezpieczeniem jest tylko gotówka + niezrealizowany wynik na CFD (wartość akcji nie jest liczona -
  założenie ostrożne). Stop out przy poziomie depozytu 50%. Odległość do stop outu = strata, która do niego
  prowadzi, podzielona przez nominał brutto CFD (jednolity ruch przeciw wszystkim pozycjom). Roczny koszt
  swapów = suma swapów / (nominał × czas utrzymywania).

## Etap 4 - stress testy i symulacje

- **Scenariusze historyczne** (2008, marzec 2020, 2022) to przybliżone, zaokrąglone ruchy od szczytu do dołka
  dziewięciu czynników: S&P 500, Nasdaq 100, WIG20, akcje europejskie (DAX), akcje globalne, złoto, ropa,
  USD/PLN, EUR/PLN. Wartości wpisano z pamięci autora o publicznie znanych poziomach indeksów; nie były tu
  weryfikowane ze źródłem. Tabela szoków jest widoczna na ekranie i w `core/stress.py`.
- **Mapowanie pozycji na czynniki**: akcje polskie → WIG20; amerykańskie → Nasdaq 100 (technologia,
  telekomunikacja) albo S&P 500; europejskie → DAX; ETF-y wg rodzaju; reszta → akcje globalne. Waluta: USD →
  USD/PLN, pozostałe waluty obce → EUR/PLN (przybliżenie). CFD na waluty i kryptowaluty są poza modelem.
- **Skalowanie betą** (domyślnie włączone): szok pozycji = beta względem jej czynnika × szok czynnika, beta
  z 504 sesji, ograniczona do [0,3; 2,5]. Strata pozycji nie przekracza jej wartości.
- **Monte Carlo**: symulowane są dzienne zwroty dzisiejszego składu portfela (z gotówką), 2 000 ścieżek,
  zawsze 10 lat (krótsze horyzonty to odczyt z tych samych ścieżek). Bootstrap blokowy: bloki 20 sesji
  z zawijaniem. Model z grubymi ogonami: niezależne zwroty t-Studenta, stopnie swobody z kurtozy.
- **Oczekiwany zwrot** w symulacji to domyślnie założenie 6% rocznie, a nie średnia z historii - dwa lata
  danych nie pozwalają wiarygodnie oszacować średniej. Można przełączyć na średnią historyczną lub wpisać własną.
- **Dopłaty** wchodzą na początku każdego miesiąca od drugiego. Obsunięcie w symulacji liczone na stopie
  zwrotu (dopłaty go nie maskują). Bez podatków i bez zmian składu portfela.
- **Co jeśli**: kapitał się nie zmienia, zmienia się podział między gotówkę a pozycję; dla CFD kwota to
  nominał i gotówka zostaje bez zmian. Bez kosztów transakcyjnych. Nowy instrument jest mapowany regułami
  automatycznymi i jego notowania są dociągane przez dostawcę.
- **Optymalizacja** (analiza, nie rekomendacja): tylko długie pozycje w akcjach i ETF-ach, które użytkownik
  już ma; wagi ≥ 0, suma 100%, limit wagi na pozycję (domyślnie 35%). Kowariancja z 504 sesji ściągnięta
  w 20% do diagonalnej; średnie zwroty ściągnięte w 50% do średniej wszystkich pozycji. Parytet ryzyka
  rozwiązywany w postaci wypukłej; HRP wg Lópeza de Prado (pojedyncze wiązanie, podział rekurencyjny).
