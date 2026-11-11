# XTB Analytics

Aplikacja na telefon do zaawansowanej analityki portfela dla klienta XTB. Wgrywasz eksport z xStation 5 i dostajesz
to, czego broker nie pokazuje: prawdziwą stopę zwrotu, ryzyko w złotówkach, stress testy, symulacje przyszłości,
analizę własnych nawyków i zestawienie do PIT-38.

Aplikacja jest wyłącznie analityczna. Nie loguje się do konta XTB, nie składa zleceń i nie daje rekomendacji
„kup / sprzedaj”.

![Ekrany aplikacji: Przegląd, Ryzyko, Symulacje, Podatki](docs/ekrany.png)

## Co potrafi

| Dział | Co tam znajdziesz |
|---|---|
| **Przegląd** | 6 kluczowych liczb, krzywa kapitału na tle wpłat, obsunięcie, aktualny skład |
| **Wyniki** | TWR i XIRR, CAGR, Sharpe, Sortino, Calmar, obsunięcia, beta i alfa; porównanie z WIG20, WIG, S&P 500 i MSCI World w PLN; zwroty miesięczne; atrybucja wyniku na pozycje, sektory, kraje i waluty |
| **Ryzyko** | VaR i CVaR 95%/99% na 1 i 10 dni trzema metodami; korelacje i udział pozycji w ryzyku; ekspozycje i koncentracja; czynniki Fama-French; dźwignia i odległość do stop outu na CFD |
| **Symulacje** | Scenariusze 2008, marzec 2020, 2022 i własne; Monte Carlo na 1/5/10 lat; „co jeśli” przed transakcją; porównanie z portfelami modelowymi |
| **Trader** (w „Więcej”) | Hit rate, payoff, profit factor, wzorce zachowań, kalkulator wielkości pozycji, koszty, reżimy rynku |
| **Podatki** (w „Więcej”) | Zestawienie do PIT-38: FIFO, kursy NBP, dywidendy zagraniczne; eksport do PDF i CSV |
| **AI** (opcjonalnie) | Memo ryzyka i pytania o portfel - pojawia się po ustawieniu klucza API |

Każda liczba ma objaśnienie prostym językiem: **dotknij wiersza**, żeby je rozwinąć.

Możesz wgrać **kilka rachunków XTB naraz** (np. w PLN i w USD), zaznaczać, które wliczać do portfela, i przełączać
**walutę wyświetlania** między PLN, USD i EUR - przyciskiem na pasku u góry ekranu.

Po uruchomieniu aplikacja pokazuje **dane przykładowe** (zmyślony portfel, syntetyczne notowania), więc możesz
obejrzeć wszystko, zanim wgrasz własny plik.

---

## Uruchomienie w chmurze (zalecane dla telefonu)

Aplikacja składa się z małego serwera w Pythonie (liczy) i strony, którą otwierasz w telefonie (pokazuje).
Najprościej postawić serwer na darmowym planie [Render](https://render.com).

1. Załóż konto na **render.com** - najwygodniej przyciskiem „Sign in with GitHub”.
2. W panelu wybierz **New → Blueprint** i wskaż repozytorium `xtb-analytics`. Jeśli Render go nie widzi, kliknij
   „Configure account” i daj mu dostęp do tego repozytorium.
3. Render odczyta plik `render.yaml` i zapyta o dwie wartości:
   - `APP_PASSWORD` - **wymyśl hasło do aplikacji**. Adres będzie publiczny, a hasło pilnuje, żeby nikt poza Tobą
     z niej nie korzystał.
   - `ANTHROPIC_API_KEY` - zostaw puste, chyba że chcesz włączyć dział AI (patrz niżej).
4. Kliknij **Apply**. Pierwsze budowanie trwa kilka minut. Na końcu dostaniesz adres w rodzaju
   `https://xtb-analytics-abcd.onrender.com`.
5. Otwórz ten adres w telefonie, wpisz hasło i dodaj aplikację do ekranu głównego (następny rozdział).

Co warto wiedzieć o darmowym planie (stan na listopad 2026): usługa zasypia po 15 minutach bez ruchu i budzi się
przy następnym wejściu, co trwa do około minuty. Aplikacja otworzy się od razu z pamięci telefonu i pokaże
„Budzę serwer…”. Po przebudzeniu notowania pobierają się od nowa, więc pierwsze przeliczenie własnego portfela
potrwa dodatkowe kilkadziesiąt sekund.

### Instalacja na telefonie

- **iPhone (Safari):** otwórz adres aplikacji → przycisk Udostępnij → „Do ekranu początkowego”.
- **Android (Chrome):** otwórz adres → menu z trzema kropkami → „Zainstaluj aplikację” lub „Dodaj do ekranu głównego”.

Od tej chwili aplikacja ma własną ikonę i otwiera się na pełnym ekranie, bez paska przeglądarki.

## Uruchomienie na własnym komputerze

Potrzebny jest Python 3.11 lub nowszy ([python.org/downloads](https://www.python.org/downloads/); w Windows zaznacz
przy instalacji „Add Python to PATH”).

| System | Komenda |
|---|---|
| macOS / Linux | `./start.sh` albo `make run` |
| Windows | dwuklik na `start.bat` |

Skrypt przy pierwszym uruchomieniu tworzy środowisko i instaluje zależności (2-5 minut), a potem wypisuje dwa adresy:
`http://localhost:8000` (ten komputer) i adres w sieci domowej, np. `http://192.168.1.20:8000`. Ten drugi otworzysz
w telefonie podłączonym do tego samego Wi-Fi. W tym wariancie dane nie opuszczają Twojego sprzętu, ale komputer musi
być włączony, a instalacja ikony z trybem offline nie zadziała (przeglądarki wymagają do tego szyfrowanego adresu
`https://`).

Hasło lokalnie jest opcjonalne. Żeby je ustawić albo włączyć AI, skopiuj `.env.example` jako `.env` i uzupełnij.

---

## Jak wyeksportować pliki z xStation 5

1. Zaloguj się do **xStation 5** na komputerze (w przeglądarce albo w aplikacji).
2. Otwórz **Historię rachunku** (Account history) - zakładka nad wykresem, obok Edukacji, Kalendarza i Wiadomości.
3. Kliknij **Eksportuj** po prawej stronie i ustaw **pełny zakres dat** - od otwarcia rachunku do dziś.
4. Zapisz plik **XLSX**. Obecny xStation zapisuje jeden plik na rachunek, np. `USD_51672516_2006-01-01_2026-11-11.xlsx`,
   z trzema arkuszami: Closed Positions, Open Positions, Cash Operations. Starsze wersje zapisują trzy osobne
   raporty (także CSV) - aplikacja czyta oba układy.
5. **Masz kilka rachunków** (np. w PLN i w USD)? Przełącz rachunek w xStation i wyeksportuj każdy osobno.
6. W aplikacji wejdź w **Więcej → Dane i import** i wgraj pliki - wszystkie naraz albo po kolei. Aplikacja sama
   rozpozna rachunki (po numerze) i tabele (po nazwach kolumn, po polsku i po angielsku).

### Kilka rachunków i waluta wyświetlania

- Każdy wgrany plik **dodaje się** do już wgranych. Nowszy eksport tego samego rachunku zastępuje starszy.
- Przycisk na pasku u góry (np. **„2 z 2 rachunków · PLN”**) otwiera wybór: które rachunki wliczać do portfela
  i w jakiej walucie pokazywać kwoty - **PLN, USD albo EUR**. Wybór jest pamiętany w telefonie.
- Przelewy między własnymi rachunkami („Currency conversion … from TA: … to: …”) nie są liczone jako wypłata
  i wpłata, gdy oba rachunki są zaznaczone. Różnica na kursie wymiany trafia do wyniku jako koszt.
- Gotówka w USD jest codziennie przeliczana po kursie NBP. Jej zmiana wartości w złotych to część wyniku
  (w atrybucji: efekt walutowy).
- Zmiana waluty wyświetlania przelicza wszystko: wartości, stopy zwrotu, benchmarki, ryzyko. Stopa zwrotu
  w dolarach i w złotych będzie inna - różni je zmiana kursu USD/PLN w tym czasie.
- **Podatki są zawsze w złotych** i obejmują tylko zaznaczone rachunki.

Ważne:

- **Operacje gotówkowe są obowiązkowe** i powinny obejmować całą historię rachunku. Z nich aplikacja odtwarza wpłaty,
  wypłaty, dywidendy i koszty. Niepełny zakres zaniży albo zawyży stopę zwrotu.
- Po wgraniu porównaj **Wartość portfela** (Więcej → Dane i import → Wczytane dane) z polem Equity w xStation
  z dnia eksportu. Jeśli się różnią, zwykle winny jest niepełny zakres dat albo zły ticker.
- Aplikacja porównuje cenę z notowań z ceną „Current price” z raportu XTB i ostrzega, gdy różnią się o ponad 8%
  (najczęściej zły ticker albo inna waluta notowań).
- Jeśli wycena którejś pozycji się nie zgadza, popraw ją w **Więcej → Dane i import → Tickery**. Dla CFD sprawdź tam
  też mnożnik i stawkę depozytu.
- Gdy plik nie zostanie rozpoznany, komunikat powie dokładnie, których kolumn brakuje.

Pliki zostają zapisane w telefonie. Kiedy zrobisz nowszy eksport, po prostu wgraj go - zastąpi starszy plik tego
samego rachunku. Rachunek usuniesz przyciskiem „Usuń” na liście rachunków.

---

## Jak czytać najważniejsze liczby

**Stopa zwrotu.** TWR mówi, jak radziły sobie Twoje decyzje, niezależnie od tego, kiedy dopłacałeś - tę liczbę
porównuj z indeksami. XIRR mówi, ile faktycznie zarobiły Twoje pieniądze; jeśli dużo dopłaciłeś tuż przed spadkiem,
będzie niższa od TWR.

**Obsunięcie (drawdown).** O ile procent portfel spadł od szczytu. To najuczciwsza miara tego, co trzeba było
wytrzymać. Liczone na stopie zwrotu, więc wpłaty go nie ukrywają.

**Sharpe, Sortino, Calmar.** Zwrot w relacji do ryzyka. Około 1 to dobry wynik, ale na dwóch latach historii te
wskaźniki mają dużą niepewność.

**VaR 95% na 1 dzień = 2 000 zł** znaczy: w 19 dni na 20 strata będzie mniejsza niż 2 000 zł. **CVaR** mówi, ile
średnio tracisz w tym jednym gorszym dniu. Jeśli trzy metody liczenia mocno się różnią, rynek ma „grube ogony”
i ostrożniej jest patrzeć na najwyższą wartość.

**Efektywna liczba zakładów.** 20 spółek, które chodzą razem, to w praktyce 2-3 zakłady. Niska wartość przy portfelu
samych akcji jest normalna - dominuje wspólny czynnik rynkowy.

**Udział w ryzyku.** Pozycja może ważyć 10% portfela, a odpowiadać za 30% jego wahań.

**Stress testy.** Co by się stało z dzisiejszym portfelem, gdyby rynki powtórzyły znany kryzys. Zwróć uwagę, że
osłabienie złotego w kryzysach amortyzuje straty na zagranicznych akcjach.

**Monte Carlo.** Wachlarz możliwych przyszłości, nie prognoza. Domyślnie zakłada 6% zwrotu rocznie, bo dwa lata
historii to za mało, żeby ufać średniej z przeszłości.

**Wzorce tradera.** Trzy typowe błędy: podbijanie stawki po stracie, trzymanie strat dłużej niż zysków i zbyt
wczesne zamykanie zyskownych pozycji. Sprawdź osobno zakres CFD i akcji.

**Podatki.** Zestawienie jest pomocą obliczeniową. Dokumentem dla urzędu jest PIT-8C od XTB; różnice wyjaśnij
z doradcą podatkowym.

Kolory: **niebieski to zysk, czerwony to strata** (para czytelna także dla osób, które słabo rozróżniają zieleń
i czerwień).

---

## Dział AI (opcjonalny)

Po ustawieniu `ANTHROPIC_API_KEY` w „Więcej” pojawia się dział **AI**: przycisk „Napisz memo ryzyka” (tygodniowy
raport w stylu risk managera) i czat odpowiadający na pytania o portfel.

- Klucz API założysz w Claude Console (instrukcja: [Get started with Claude](https://platform.claude.com/docs/en/get-started)). Na Render wpisz go w ustawieniach usługi
  (Environment), lokalnie w pliku `.env`. Korzystanie z API jest płatne wg cennika Anthropic.
- Do modelu trafiają **wyłącznie zagregowane metryki** i nazwy najważniejszych pozycji. Nie trafia tam imię
  i nazwisko, numer rachunku ani lista transakcji. Dokładną zawartość zobaczysz w aplikacji pod „Co dokładnie
  zostanie wysłane”, zanim cokolwiek wyślesz.
- Model można zmienić zmienną `ANTHROPIC_MODEL` (domyślnie `claude-sonnet-5-5`).
- Bez klucza aplikacja działa w pełni, tylko bez tego działu.

## Prywatność i bezpieczeństwo

- Eksport z XTB jest zapisany **w telefonie**. Na serwer trafia tylko na czas obliczeń i żyje tam wyłącznie
  w pamięci - nie jest zapisywany na dysk ani do bazy.
- Serwer pobiera notowania (Yahoo Finance, Stooq), kursy NBP i czynniki z biblioteki K. Frencha. Wysyła przy tym
  tylko symbole instrumentów.
- W chmurze **zawsze ustaw `APP_PASSWORD`**. To jedno wspólne hasło, nie system kont - nie udostępniaj adresu
  z hasłem osobom, którym nie pokazałbyś swojego portfela.
- „Usuń moje dane z tego telefonu” w Ustawieniach kasuje zapisany eksport i poprawki tickerów.

---

## Dla programisty

```
core/     obliczenia (bez zależności od serwera i UI): portfolio, performance, attribution, risk, factors,
          cfd, stress, montecarlo, whatif, optimize, trader, regimes, tax, ai
data/     parser eksportu XTB, mapowanie tickerów, cache SQLite, dostawcy notowań, syntetyczny rynek demo
server/   Flask: API, sesje w pamięci, ekrany składane z klocków (server/blocks.py, server/screens/)
web/      interfejs mobilny (PWA): czysty HTML/CSS/JS, własne wykresy SVG, bez etapu budowania
tests/    pytest: silnik, API, interfejs w przeglądarce (Playwright)
```

- **Testy:** `make test`. Test interfejsu (`tests/test_ui.py`) wymaga `playwright install chromium`; bez przeglądarki
  jest pomijany.
- **Nowy ekran** to funkcja w `server/screens/`, która zwraca listę klocków (zdanie, metryki, wykres, tabela,
  kontrolki), plus wpis w `SCREENS` i `NAV`. Front rysuje klocki sam - nie trzeba dotykać JavaScriptu.
- **Dane przykładowe:** `make sample` generuje `data/sample/xtb_sample_export.xlsx`.
- **Inny hosting:** w repozytorium jest `Dockerfile`. Serwer musi działać w jednym procesie (`--workers 1`), bo
  sesje żyją w pamięci.
- Wszystkie przyjęte założenia są w [ASSUMPTIONS.md](ASSUMPTIONS.md).

## Znane ograniczenia

**Czego nie dało się sprawdzić podczas budowy**

- Aplikacja powstała w środowisku bez internetu. **Pobieranie notowań na żywo** (yfinance, Stooq, NBP, biblioteka
  K. Frencha), **serwer produkcyjny gunicorn** i **wdrożenie na Render** nie były tam uruchamiane - przetestowane są
  parsery odpowiedzi, cache i logika zapasowego źródła. Pierwsze uruchomienie na prawdziwych danych jest pierwszym
  realnym testem tych połączeń.
- **Parser** jest testowany na plikach odtwarzających prawdziwy układ eksportu xStation z listopada 2026
  (arkusze Closed Positions / Open Positions / Cash Operations, rachunek PLN i USD) oraz na starszym układzie
  i wariantach formatu (CSV, polskie nagłówki, przecinki dziesiętne, osobne pliki). Pliki testowe powstały na
  podstawie zrzutów ekranu, nie z oryginalnego pliku - rzadkie typy operacji mogą wymagać dopisania. Gdy czegoś
  brakuje, zobaczysz komunikat, których kolumn; listę nazw łatwo rozszerzyć w `data/xtb_parser.py`.
- Yahoo Finance bywa niedostępne z adresów serwerowni; wtedy aplikacja sięga do Stooq, a przy braku notowań wycenia
  pozycję po cenie zakupu i pokazuje ostrzeżenie.

**Dane i wycena**

- Rachunki w PLN, USD i EUR można łączyć. Kursy do przeliczeń to średnie kursy NBP z dnia operacji, a nie kurs,
  po którym XTB faktycznie wymienił walutę - drobne różnice trafiają do pozycji „gotówka” w atrybucji.
- Godziny w nowym eksporcie są w UTC. Transakcja tuż po północy czasu polskiego może trafić do poprzedniego dnia.
- Przy rachunkach z różnymi datami raportu portfel jest liczony na datę najnowszego.
- Mnożniki i stawki depozytu CFD pochodzą z tabeli wbudowanej w aplikację - sprawdź je dla swoich instrumentów
  w zakładce Tickery.
- Ceny są korygowane o splity przez źródło danych, ilości w eksporcie nie - split w trakcie trzymania pozycji może
  zniekształcić wycenę historyczną.
- Dni wolne na jednej giełdzie przy otwartej drugiej są wypełniane ostatnim zamknięciem.
- WIG zawiera dywidendy, WIG20 i S&P 500 nie - porównanie z nimi jest dla portfela lekko korzystne.

**Modele**

- Dwa lata historii to mało. Sharpe, alfa, bety czynnikowe i „maksymalny Sharpe” w optymalizacji mają dużą
  niepewność.
- Scenariusze historyczne to zaokrąglone ruchy indeksów i kursów, wpisane bez weryfikacji ze źródłem.
- Czynniki Fama-French opisują rynek USA; dla spółek z GPW to przybliżenie.
- Reżimy rynku są wyznaczane z perspektywy czasu i nie służą do prognoz.
- Spread i koszt przewalutowania to szacunki (0,5% i stawki z tabeli tickerów).

**Podatki**

- FIFO jest liczone osobno dla każdego rachunku. Na rachunku walutowym prowizje, opłaty i wyniki CFD są
  przeliczane po kursie NBP z dnia operacji (nie z dnia poprzedniego).
- To pomoc obliczeniowa, nie porada. Nie obejmuje strat z lat ubiegłych, rachunków u innych brokerów, IKE/IKZE ani pozycji
  przeniesionych od innego brokera. Za datę transakcji przyjęto datę z raportu, nie rozliczenia.
- Kraj do PIT/ZG pochodzi z tabeli tickerów; dla ETF-ów trzeba go poprawić na kraj rejestracji funduszu.

**Aplikacja**

- Obliczenia wymagają połączenia z serwerem. Bez sieci otworzy się tylko powłoka aplikacji.
- Sesje żyją w pamięci serwera (do 6 naraz, 3 godziny bezczynności). Po restarcie telefon odtwarza sesję sam
  z zapisanego pliku.
- iPhone może po dłuższym nieużywaniu wyczyścić pamięć strony - wtedy trzeba wgrać eksport ponownie.

## Licencje dołączonych krojów pisma

- `web/fonts/ledger-serif*.woff` - podzbiór znaków kroju Lora (SIL Open Font License 1.1), pod zmienioną nazwą
  zgodnie z licencją; tekst licencji w `web/fonts/OFL.txt`.
- `server/fonts/DejaVuSans*.ttf` - krój DejaVu (licencja Bitstream Vera), używany w PDF; tekst licencji
  w `server/fonts/LICENSE-DejaVu.txt`.
