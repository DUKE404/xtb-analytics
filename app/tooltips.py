"""Podpowiedzi 'co to znaczy' - każda metryka wyjaśniona prostym językiem."""

TOOLTIPS = {
    # --- Przegląd
    "Wartość portfela": "Ile dziś warte jest wszystko na rachunku: gotówka + akcje i ETF-y po bieżących cenach "
                        "+ niezrealizowany wynik na CFD. W złotych.",
    "Wynik całkowity": "Ile zarobiłeś lub straciłeś w złotych od początku: obecna wartość portfela minus suma wpłat "
                       "plus suma wypłat. Uwzględnia dywidendy, prowizje, swapy i kursy walut.",
    "Wpłaty netto": "Suma wszystkich wpłat na rachunek minus wypłaty. To Twój własny kapitał włożony w portfel.",
    "Stopa zwrotu (TWR)": "Stopa zwrotu ważona czasem. Pokazuje, jak radziły sobie Twoje decyzje inwestycyjne, "
                          "niezależnie od tego, kiedy i ile dopłacałeś. To właściwa miara do porównań z indeksami.",
    "CAGR": "Średnioroczna stopa zwrotu: o ile procent rocznie rósłby portfel, gdyby rósł równo każdego roku.",
    "MWR / XIRR": "Stopa zwrotu ważona pieniądzem. Uwzględnia moment i wielkość Twoich wpłat - odpowiada na pytanie "
                  "'ile procent rocznie zarobiły faktycznie moje pieniądze'. Jeśli dużo dopłaciłeś tuż przed spadkiem, "
                  "będzie niższa niż TWR.",
    "Zmienność": "Jak mocno portfel 'buja się' z dnia na dzień, w skali roku. 15% oznacza, że w typowym roku wynik "
                 "odchyla się od średniej o ok. 15 punktów procentowych. Im wyżej, tym bardziej nerwowa jazda.",
    "Sharpe": "Ile zwrotu ponad bezpieczną lokatę dostajesz za jednostkę ryzyka. Poniżej 0,5 słabo, ok. 1 dobrze, "
              "powyżej 2 rzadko się utrzymuje. Na krótkiej historii bywa mylący.",
    "Sortino": "Jak Sharpe, ale karze tylko za wahania w dół. Wahania w górę nikomu nie przeszkadzają.",
    "Calmar": "Średnioroczny zwrot podzielony przez największe obsunięcie. Mówi, ile zarabiasz na każdy procent "
              "najgorszego spadku, jaki musiałeś wytrzymać.",
    "Max drawdown": "Największe obsunięcie: o ile procent portfel spadł od szczytu do dołka w najgorszym momencie. "
                    "To realistyczna miara bólu, na jaki trzeba być gotowym.",
    "Czas obsunięcia": "Ile dni trwało najdłuższe obsunięcie - od szczytu do momentu, gdy portfel wrócił na ten sam poziom.",
    "Czas odrabiania": "Ile dni zajęło odrobienie największego spadku, licząc od dołka do powrotu na poprzedni szczyt.",
    "Beta": "Wrażliwość na rynek. Beta 1,2 oznacza, że gdy indeks spada o 10%, portfel spada typowo o ok. 12%. "
            "Poniżej 1 - portfel spokojniejszy od rynku.",
    "Alfa": "Część wyniku, której nie tłumaczy samo podążanie za rynkiem (w skali roku). Dodatnia alfa to wartość "
            "dodana ponad to, co dałby indeks przy tej samej becie. Na krótkiej historii to często szum.",
    "Tracking error": "Jak bardzo wyniki portfela odbiegają od benchmarku z dnia na dzień (w skali roku). "
                      "Niski = portfel zachowuje się jak indeks.",
    "Information ratio": "Nadwyżka zwrotu nad benchmarkiem podzielona przez tracking error. Powyżej 0,5 to dobra, "
                         "konsekwentna przewaga nad indeksem.",
    "Skośność": "Asymetria wyników dziennych. Ujemna: zdarzają się rzadkie, ale duże straty (typowe dla akcji). "
                "Dodatnia: rzadkie duże zyski.",
    "Kurtoza": "Jak często zdarzają się dni skrajne. Powyżej 0 - 'grube ogony': duże ruchy trafiają się częściej, "
               "niż wynikałoby z rozkładu normalnego, więc proste modele ryzyka zaniżają straty.",
    "Najlepszy dzień": "Najwyższa dzienna stopa zwrotu portfela w historii.",
    "Najgorszy dzień": "Najniższa dzienna stopa zwrotu portfela w historii.",
    "Dni na plusie": "Odsetek dni, w których portfel zamknął się wyżej niż dzień wcześniej.",
    # --- Ryzyko
    "VaR": "Value at Risk: strata, której przy danym poziomie ufności nie powinieneś przekroczyć w danym horyzoncie. "
           "VaR 95% 1 dzień = 2 000 zł znaczy: w 19 dni na 20 strata będzie mniejsza niż 2 000 zł. Nie mówi, jak źle "
           "bywa w tym jednym dniu z dwudziestu.",
    "CVaR": "Średnia strata w tych najgorszych dniach, które wypadają poza VaR. Odpowiada na pytanie: 'jeśli już jest "
            "źle, to jak bardzo?'. Zawsze większa niż VaR.",
    "VaR historyczny": "Liczony wprost z rzeczywistych dziennych zmian cen z przeszłości - bez założeń o rozkładzie.",
    "VaR parametryczny": "Liczony ze średniej i zmienności przy założeniu rozkładu normalnego. Szybki, ale zaniża "
                         "ryzyko, gdy rynek ma grube ogony.",
    "VaR Monte Carlo": "Liczony z tysięcy losowych scenariuszy zgodnych z korelacjami między Twoimi pozycjami.",
    "Efektywna liczba zakładów": "Ile naprawdę niezależnych źródeł ryzyka masz w portfelu. 20 spółek, które chodzą "
                                 "razem, to w praktyce 2-3 zakłady. Im wyżej, tym lepsza dywersyfikacja.",
    "Udział w ryzyku": "Jaka część wahań całego portfela pochodzi z danej pozycji. Pozycja może ważyć 10% portfela, "
                       "a odpowiadać za 30% ryzyka, jeśli jest zmienna i skorelowana z resztą.",
    "Średnia korelacja": "Jak bardzo pozycje poruszają się razem (od -1 do 1). Blisko 1 - wszystko rośnie i spada "
                         "jednocześnie, dywersyfikacja jest pozorna.",
    "Współczynnik dywersyfikacji": "Średnia zmienność pozycji podzielona przez zmienność portfela. 1 = brak korzyści "
                                   "z dywersyfikacji, 2 = portfel o połowę spokojniejszy niż jego składniki.",
    "HHI": "Indeks koncentracji Herfindahla: suma kwadratów udziałów. 0,10 odpowiada 10 równym pozycjom, 0,25 - czterem. "
           "Im wyżej, tym bardziej portfel zależy od kilku pozycji.",
    "Top 5": "Jaką część portfela stanowi pięć największych pozycji.",
    "Dni do zamknięcia": "Ile sesji zajęłoby wyjście z pozycji, gdybyś sprzedawał nie więcej niż 20% średniego dziennego "
                         "obrotu, żeby nie zbijać ceny. Poniżej 1 dnia = pozycja płynna.",
    "Efektywna dźwignia": "Łączna ekspozycja (akcje + nominał CFD) podzielona przez kapitał. 1,5x oznacza, że 1% ruchu "
                          "rynku zmienia Twój kapitał o ok. 1,5%.",
    "Poziom depozytu": "Kapitał zabezpieczający CFD podzielony przez wymagany depozyt. XTB zamyka pozycje automatycznie, "
                       "gdy spadnie do 50% (stop out).",
    "Odległość do margin call": "O ile procent musiałyby ruszyć się wszystkie pozycje CFD przeciwko Tobie, żeby broker "
                                "zaczął je zamykać. Liczone ostrożnie: zabezpieczeniem jest tylko gotówka i wynik na CFD.",
    "Roczny koszt swapów": "Ile w skali roku kosztuje utrzymywanie otwartych pozycji CFD przez noc (punkty swapowe), "
                           "jako procent ich wartości nominalnej.",
    "Ekspozycja walutowa": "Jaka część portfela zmienia wartość razem z kursem danej waluty do złotego.",
    "R²": "Jaka część wahań portfela jest wyjaśniona przez czynniki w modelu. 0,8 = 80% zachowania portfela to "
          "znane czynniki rynkowe, reszta to specyfika Twoich spółek.",
    # --- Symulacje
    "Prawdopodobieństwo celu": "W ilu procentach symulowanych przyszłości portfel osiągnął wskazaną wartość na koniec horyzontu.",
    "Prawdopodobieństwo obsunięcia": "W ilu procentach symulowanych przyszłości portfel choć raz spadł od szczytu o więcej niż wskazany próg.",
    "Mediana": "Wynik środkowy: połowa scenariuszy kończy się wyżej, połowa niżej.",
    # --- Trader
    "Hit rate": "Odsetek transakcji zakończonych zyskiem. Sam w sobie nic nie mówi - liczy się w parze ze stosunkiem "
                "średniego zysku do średniej straty.",
    "Średni zysk": "Średni wynik transakcji zyskownej (po kosztach).",
    "Średnia strata": "Średni wynik transakcji stratnej (po kosztach).",
    "Profit factor": "Suma zysków podzielona przez sumę strat. Powyżej 1 - strategia zarabia; 1,5 i więcej to solidny wynik.",
    "Expectancy": "Oczekiwany wynik jednej transakcji: ile średnio zarabiasz (lub tracisz) za każdym razem, gdy otwierasz pozycję.",
    "Payoff ratio": "Średni zysk podzielony przez średnią stratę. Poniżej 1 - musisz mieć rację wyraźnie częściej niż w połowie transakcji.",
    "Czas trzymania": "Jak długo pozycje były otwarte. Jeśli stratne trzymasz dużo dłużej niż zyskowne, to klasyczny "
                      "'efekt dyspozycji': szybko realizujesz zyski, a stratom dajesz rosnąć.",
    "Kelly": "Wzór Kelly'ego podaje ułamek kapitału, który maksymalizuje długoterminowy wzrost przy danej skuteczności "
             "i stosunku zysk/strata. W praktyce stosuje się połowę - pełny Kelly oznacza bardzo głębokie obsunięcia.",
    "Koszty": "Wszystko, co oddałeś po drodze: prowizje, spready (szacunek), swapy, przewalutowania.",
    # --- Podatki
    "Przychód": "Suma wartości sprzedaży przeliczona na złote po kursie NBP z dnia roboczego poprzedzającego transakcję.",
    "Koszty uzyskania": "Koszt zakupu sprzedanych papierów (metodą FIFO - pierwsze kupione, pierwsze sprzedane) plus prowizje.",
    "Dochód": "Przychód minus koszty uzyskania przychodu. Od dodatniego dochodu płaci się 19% podatku.",
}
