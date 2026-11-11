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

## Etap 5 - analityka tradera i koszty

- **Transakcja** = jedna zamknięta pozycja z raportu. Wynik netto = wynik brutto + prowizja + swap + rollover.
  Wielkość: dla akcji/ETF wartość zakupu z raportu, dla CFD nominał (loty × mnożnik × cena × kurs NBP).
- **Godziny i dni tygodnia** wg czasu z raportu XTB, bez przeliczania stref.
- **Większe pozycje po stracie**: wielkość normalizowana medianą wielkości na tym samym instrumencie;
  „poprzednia” transakcja to ostatnia zamknięta przed otwarciem bieżącej. Wzorzec = stosunek ≥ 1,2 i p < 0,05
  (test Manna-Whitneya).
- **Efekt dyspozycji**: mediana czasu trzymania strat ≥ 1,5 × mediana dla zysków i p < 0,05.
- **Zbyt wczesna realizacja zysków**: ruch ceny przez 20 sesji po zamknięciu zyskownej transakcji, w kierunku
  pozycji; wzorzec = średni ruch > 0, ponad 55% przypadków na korzyść i p < 0,10 (test t).
- **Wielkość pozycji** (kalkulator): kapitał × ryzyko ÷ (k × zmienność dzienna × √dni), zmienność z 120 sesji.
  Kelly: f = p − (1 − p)/b, w aplikacji pokazana także połowa; odnosi się do kapitału ryzykowanego w transakcji.
- **Koszty**: prowizje i swapy z raportu. Spread = połowa spreadu z tabeli tickerów od każdej nogi transakcji
  (wartości domyślne są szacunkiem autora: akcje GPW 15 pb, USA 4 pb, ETF 8 pb, CFD na indeksy 2,5 pb).
  Przewalutowanie = 0,5% od kupna i sprzedaży akcji/ETF w walucie obcej - stawka przyjęta z pamięci, do
  sprawdzenia w aktualnej tabeli opłat XTB. Spread i przewalutowanie są już zawarte w cenach transakcji.
- **Reżimy rynku**: wyznaczane na benchmarku z ustawień. HMM z rozkładami normalnymi (Baum-Welch + Viterbi,
  własna implementacja), stany uporządkowane po zmienności; alternatywnie progi zmienności z 21 sesji
  (67. percentyl przy 2 stanach; 50. i 85. przy 3). Reżimy są wyznaczone ex post - nie służą do prognoz.

## Etap 6 - podatki (PIT-38)

- To **pomoc obliczeniowa, nie porada podatkowa** - adnotacja jest na ekranie, w PDF i w CSV. XTB jako polski
  dom maklerski wystawia PIT-8C i to on jest dokumentem dla urzędu; zestawienie służy do zrozumienia i kontroli.
- **FIFO** liczone osobno dla każdego instrumentu po wszystkich zakupach z eksportu (zamknięte i otwarte
  pozycje), niezależnie od tego, którą pozycję XTB wskazał jako zamkniętą.
- **Kurs**: średni NBP (tabela A) z ostatniego dnia publikacji przed dniem transakcji, osobno dla zakupu
  i sprzedaży. Za dzień transakcji przyjęto datę z raportu XTB, nie datę rozliczenia (T+2).
- **Koszty**: koszt zakupu wg FIFO + prowizje od zamkniętych pozycji (w PLN z raportu). Koszt przewalutowania
  XTB nie jest osobną pozycją kosztów.
- **CFD**: kwoty w PLN z raportu; zyski = przychód, straty + swapy + prowizje = koszty, wg roku zamknięcia.
- **Dywidendy zagraniczne**: 19% brutto minus podatek u źródła (maks. 19% brutto), liczone per kraj,
  zaokrąglane łącznie do pełnych złotych. Gdy komentarz operacji podaje kwotę na akcję w walucie, a stan
  posiadania z eksportu zgadza się z wypłatą (±10%), kwota brutto jest przeliczana po kursie NBP D-1; w innym
  razie brana jest kwota PLN z raportu. Dywidendy polskie pokazane informacyjnie (podatek pobiera płatnik).
- **Kraj do PIT/ZG** pochodzi z tabeli tickerów (dla ETF-ów to obszar inwestycji, nie kraj rejestracji
  funduszu - do poprawienia ręcznie).
- **Poza zakresem**: straty z lat ubiegłych, inne rachunki, pozycje przeniesione od innego brokera, konta
  IKE/IKZE, numery pól formularza (zmieniają się co roku).
- **Wariant porównawczy** „Kwoty z raportu XTB” używa wartości PLN przeliczonych przez brokera.

## Etap 7 - AI i wdrożenie

- **AI**: wywołanie wprost do Messages API (`POST /v1/messages`, nagłówki `x-api-key` i `anthropic-version:
  2023-06-01`) biblioteką `requests`, bez dodatkowego SDK. Model domyślny `claude-sonnet-5-5`, zmieniany zmienną
  `ANTHROPIC_MODEL`. Wywołanie nie było wykonywane podczas budowy (brak sieci i klucza) - przetestowany jest
  kształt zapytania, obsługa błędów i interfejs z podstawionym modelem.
- **Co trafia do modelu**: tylko paczka z `server/ai_payload.py` - wartości zbiorcze portfela, wskaźniki, udziały
  i nazwy kilku pozycji o największym wpływie. Bez imienia i nazwiska, numeru rachunku, transakcji i operacji
  gotówkowych. Paczkę można podejrzeć w aplikacji przed wysłaniem. Kwoty w PLN są w niej zawarte, bo bez nich
  memo ryzyka nie miałoby treści.
- **Zasady dla modelu**: tylko liczby z paczki, żadnych rekomendacji kupna/sprzedaży, treść pól tekstowych
  traktowana jako dane, a nie polecenia.
- **Hosting**: Render, plan darmowy, region Frankfurt, jeden proces gunicorn z 4 wątkami (sesje w pamięci
  wymagają jednego procesu). Cache notowań leży na dysku ulotnym - po uśpieniu instancji pobiera się od nowa.
- **Pobieranie notowań** u dostawcy „na żywo” idzie w 4 wątkach naraz (zmienna `FETCH_WORKERS`), żeby pierwsze
  przeliczenie po przebudzeniu serwera było krótsze.
- **Monte Carlo** losuje zwroty miesiąc po miesiącu, żeby zmieścić się w 512 MB pamięci darmowej instancji.

## Po etapie 7 - prawdziwy eksport, kilka rachunków, waluta wyświetlania

Powód: pierwszy prawdziwy plik użytkownika (`USD_51672516_2006-01-01_2026-11-11.xlsx`) miał inny układ niż
założony w etapie 1, rachunek był w USD, a użytkownik ma też drugi rachunek w PLN i chce je łączyć.

- **Układ pliku** odtworzono ze zrzutów ekranu obu rachunków: metadane nad nagłówkiem, kolumna `Ticker`
  zamiast `Symbol`, czasy z dopiskiem „(UTC)”, w otwartych pozycjach wiersz zbiorczy instrumentu nad wierszami
  pozycji, na końcu zamkniętych wiersz sumy. Pliki testowe (`tests/real_layout.py`) są zbudowane według tych
  zrzutów; oryginalnego pliku nie miałem.
- **Waluta rachunku** jest brana z arkusza otwartych pozycji (kolumna Currency przy „Open position value”),
  a gdy jej tam nie ma - z początku nazwy pliku (`USD_…`). Numer rachunku: z wiersza „Account number”.
- **Data raportu**: „Data as of report generated”. Daty „Date from 2006-01-01” nie biorę za początek historii -
  portfel zaczyna się od pierwszej operacji.
- **Otwarte pozycje** nie mają wartości zakupu; przyjmuję wartość rynkowa minus wynik brutto.
- **Podpowiedzi z raportu**: nazwa i kategoria instrumentu idą do tabeli tickerów. Gdy wartość pozycji równa się
  cena × ilość, instrument jest notowany w walucie rachunku (tak rozpoznaję np. SMH.UK jako ETF w USD).
  Kolejność: poprawki użytkownika > wbudowana tabela > podpowiedzi z raportu > reguły z końcówki symbolu.
- **Kilka rachunków**: pliki są grupowane po numerze rachunku. Kilka plików xlsx tego samego rachunku - liczy
  się najnowszy raport (starszy powtarzałby te same operacje).
- **Przelewy wewnętrzne**: operacja `Transfer` z komentarzem „from TA: A to: B”, gdy oba rachunki są zaznaczone,
  nie jest przepływem zewnętrznym. Obie nogi są wyceniane kursem NBP z dnia przelewu, więc różnica (marża
  wymiany XTB) wychodzi jako mały ujemny wynik w koszyku gotówki. Gdy zaznaczony jest jeden rachunek, ten sam
  przelew jest dla niego wpłatą albo wypłatą.
- **Gotówka w kilku walutach**: saldo prowadzę osobno w walucie każdego rachunku i wyceniam codziennie kursem
  NBP. Zmiana wartości salda z samego kursu jest częścią wyniku (koszyk gotówki; w atrybucji jako efekt walutowy).
- **Waluta wyświetlania** (PLN / USD / EUR) to waluta bazowa całego silnika, nie przeliczenie gotowych liczb:
  kursy krzyżowe przez złotego z tabeli NBP, benchmarki przeliczone na bazę, VaR i scenariusze w bazie. W scenariuszach
  szok „USD/PLN +15%” przy bazie USD oznacza spadek wartości aktywów złotowych o 1/1,15 − 1.
- **Koszt przewalutowania** (szacunek 0,5%) dotyczy tylko instrumentów w walucie, w której nie ma zaznaczonego
  rachunku - akcje w USD kupowane z rachunku USD nie są przewalutowywane.
- **Podatki** zawsze w PLN, z kursów NBP, niezależnie od waluty wyświetlania. FIFO osobno na każdym rachunku.
  Kwoty z rachunku walutowego, których nie da się policzyć z ceny instrumentu (prowizje, opłaty, CFD, dywidendy
  bez kwoty na akcję), przeliczam kursem NBP z dnia operacji - uproszczenie względem D-1.
- **Kontrola wyceny**: ostrzeżenie, gdy ostatnia cena z notowań różni się o ponad 8% od „Current price” z raportu.
- **Domyślna waluta wyświetlania to PLN** także dla samego rachunku USD (polski podatnik, podatki w złotych).
- **Czas UTC** z eksportu traktuję jak czas lokalny przy przypisaniu do dnia.

### Poprawka po pierwszym wczytaniu prawdziwych danych (TWR −100%, wartość portfela chwilowo ujemna)

Na prawdziwych danych krzywa kapitału zeszła na kilka dni poniżej zera, co wyzerowało TWR na zawsze. Samymi
akcjami nie da się zejść poniżej zera (gotówka + wartość ≥ 0), więc źródłem musi być pozycja krótka albo CFD
wyceniony jak akcja lub po złym poziomie cen. Pliku nie widziałem, dlatego poprawka obejmuje całą klasę błędów:

- **CFD po kategorii**: każda kategoria inna niż STOCK/ETF to CFD (wartość = wynik, nie cena × ilość). Wcześniej
  CFD na akcję o tickerze znanej spółki był liczony jak akcja - krótka pozycja dawała wtedy ujemną „wartość”.
- **CFD na akcję** dostaje symbol `TICKER_4`; operacje gotówkowe są przypisywane po kategorii wiersza.
- **Mnożnik CFD z raportu**: wynik / (kierunek × wolumen × zmiana ceny × kurs przeliczenia), mediana po zamkniętych
  pozycjach, zaokrąglona do typowych wartości. Depozyt: Margin / nominał.
- **Kotwiczenie wyceny**: ceny transakcji z raportu są pewne, notowania dają kształt ścieżki. Gdy notowania
  w dniu otwarcia lub zamknięcia odbiegają od ceny transakcji o więcej niż 25% (akcje) albo 2% (CFD), pozycja
  jest wyceniana notowaniami przeskalowanymi (interpolacja logarytmiczna współczynnika między oboma końcami).
  Dla otwartych pozycji końcem jest „Current price” z raportu.
- **Ujemna wartość portfela** to błąd danych: taki dzień i dzień powrotu mają stopę zwrotu 0, a ostrzeżenie
  podaje daty i pozycję o największym ujemnym wkładzie.
- **Powłoka aplikacji** ładuje się najpierw z sieci (2,5 s na odpowiedź), dopiero potem z pamięci telefonu -
  po wdrożeniu nowa wersja jest od razu, bez dwukrotnego otwierania.

## Widok portfela, moduł Spółki, układ na komputer

- **CFD wyceniane wynikiem z raportu**: niezrealizowany wynik CFD = wynik brutto z raportu × część drogi ceny
  od otwarcia do ceny końcowej (zamknięcia albo bieżącej). Nie zależy od mnożnika, waluty instrumentu ani
  poziomu notowań. Gdy raport nie podaje wyniku (albo jest poniżej 1 jednostki waluty), zostaje wzór z mnożnikiem.
- **Pomiń CFD**: pozycje CFD znikają, a ich operacje gotówkowe (symbol CFD albo typ close trade / swap /
  rollover) stają się przepływem zewnętrznym. To wydzielenie portfela akcyjnego, nie usunięcie danych -
  saldo gotówki zostaje zgodne z rachunkiem. Podatki liczą wszystko.
- **Okres analizy** to wspólne ustawienie widoku (nie parametr pojedynczego ekranu): portfel jest liczony raz
  na pełnej historii i przycinany, więc stopy dzienne w oknie są identyczne z pełną historią. Kapitałem
  początkowym jest wartość z dnia przed początkiem. Minimalna długość okna: 2 tygodnie.
- **Dlaczego to ważne przy małym rachunku na starcie**: TWR waży każdy dzień tak samo niezależnie od kapitału,
  więc strata 90% na rachunku 500 zł zostaje w krzywej i w max drawdown na zawsze. Okno analizy i pomijanie
  CFD to dwa uczciwe sposoby, żeby ocenić to, czym portfel jest dziś.
- **Benchmarki**: WIG usunięty (Yahoo go nie ma, Stooq przestał odpowiadać), dodany Nasdaq 100 (^NDX).
- **Spółki**: źródłem jest yfinance (info, income_stmt, cashflow, revenue_estimate, earnings_estimate,
  analyst_price_targets), cache 24 h. Każdy fragment pobierany osobno - brak jednego nie psuje reszty.
  Odwrócony DCF: FCF z ostatnich 12 miesięcy rośnie w stałym tempie przez 5/10/15 lat, potem wzrost wieczny
  (Gordon); szukane tempo zrównuje wartość z EV (gdy brak EV - z kapitalizacją). Domyślnie dyskonto 10%,
  wzrost wieczny 2,5%. Ceny docelowe i „nastawienie analityków” są opisane jako cudze opinie; aplikacja
  dalej niczego nie rekomenduje.
- **Kolory**: zysk zielony, strata czerwona (konwencja rynkowa) - zawsze razem ze znakiem +/−, więc kolor nie
  jest jedynym nośnikiem informacji. W tabelach czerwone są wszystkie wartości ujemne.
- **Układ na komputer** od 1024 px: ta sama treść i te same bloki, inny układ (CSS), bez osobnego kodu ekranów.
