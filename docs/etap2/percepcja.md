# Percepcja: informacje o otoczeniu, ograniczenia i jakość danych (#11)

Percepcja to część systemu, która zamienia dane o wydziale i zmiany w trakcie semestru na stan, z którego układa się plan. Ten dokument opisuje:
- jakie informacje o otoczeniu system ma (wymagany rezultat etapu 2);
- czego percepcja nie wie albo wie tylko w przybliżeniu i jak to wpływa na plan;
- automatyczny raport jakości danych, który te problemy liczy.

```bash
uv run krok-quality --data data/generated/kierunek-szum --events data/generated/kierunek/events.jsonl
uv run krok-quality --db data/krok.db                     # baza po krok-db import / events
```

## Informacje o otoczeniu dostępne dla systemu

| Informacja | Plik / źródło | Skąd pochodzi | Na ile pewna |
|---|---|---|---|
| Budynki P1–P6 i ich współrzędne | `buildings.json` | ręcznie z rzeczywistości (#6) | współrzędne przybliżone (L1) |
| Sale: numer, typ, pojemność | `rooms.json` | układ sal prawdziwy (#6), typy i pojemności wymyślone | dane testowe (L8), brakująca pojemność (L4) |
| Zasady: godziny, przerwy, reguły, wagi, prędkość marszu, `routeFactor` | `config.json` | ustala planista, reguły z [kryteriów](../etap1/kryteria.md) | pewne: to decyzje, nie pomiary |
| Kalendarz: semestr, święta, dni rektorskie, odpracowania, blokady, zjazdy zaoczne | `calendar.json` | władze uczelni, generator (#7) | pewne w chwili ogłoszenia, zmienia się w trakcie (zdarzenia) |
| Struktura studentów: kierunki, programy, drzewo grup, liczebności | `faculties.json`, `groups.json` | generator (#7), docelowo dziekanat | liczebność szacowana przed zapisami (L3) |
| Prowadzący: kompetencje, budynek macierzysty, dostępność w godzinach, limit | `lecturers.json` | generator (#7), docelowo deklaracje prowadzących | dostępność deklarowana (L2) |
| Przedmioty, obsada (`lecturerPools`) i zajęcia do zaplanowania | `subjects.json`, `classes.json` | generator (#7), docelowo program studiów | pewne po zatwierdzeniu programu |
| Zmiany w trakcie semestru: blokady sal, nieobecności, nowa dostępność, liczebności, nowe grupy i przedmioty, wyjątki | `events.jsonl` | strumień zdarzeń (#8) | mogą być spóźnione, powtórzone albo błędne |

Ścieżka danych w systemie:

1. **Import i czyszczenie (#9).** Pliki trafiają do bazy SQLite. Każdy problem jest zapisany w `import_issues`, a import idzie dalej.
2. **Zdarzenia.** Strumień zdarzeń zmienia stan w bazie. Każda linia trafia do dziennika ze statusem.
3. **Stan otoczenia (#10).** Z danych w bazie powstają macierze czasu przejść i dostępności, sale wg typu i pojemności oraz raport wykonalności ([stan-otoczenia.md](stan-otoczenia.md)).
4. **Raport jakości (#11).** Zbiera problemy z obu poprzednich kroków i mierzy, jak bardzo plan zależy od niepewnych danych.

**Czego system nie widzi.** Nie ma informacji o faktycznej frekwencji, wyposażeniu sal poza ich typem, preferencjach prowadzących poza dostępnością, rzeczywistych trasach między budynkami ani o awariach, dopóki ktoś ich nie zgłosi zdarzeniem. Plan jest tak dobry, jak te dane. Raport jakości pokazuje, gdzie ta zależność jest największa.

## Katalog ograniczeń percepcji

| | Ograniczenie | Wpływ na system | Jak łagodzimy | Miara w raporcie jakości |
|---|---|---|---|---|
| L1 | **Współrzędne budynków są przybliżone.** | Czas przejścia może być niedokładny, a z nim reguła `czas-przejscia`: plan może wymagać za krótkiej albo za długiej przerwy. | Czas przejść ma zapas w `routeFactor`, a minimalna przerwa to reguła w konfiguracji. | błąd czasu przejścia przy błędzie współrzędnych ±e m (domyślnie 30 m) i liczba par budynków z błędem ≥ 20% |
| L2 | **Deklarowana dostępność ≠ faktyczna.** | Prowadzący, który uczy mniej, niż zadeklarował, może zrobić plan niewykonalnym albo wymusić zastępstwa. | Generator i obsada mają zapas (`slack`). Zmiany przychodzą zdarzeniem `lecturerAvailabilityChanged`. | kompetencje, które wykorzystują ≥ 80% dostępności prowadzących, i o ile dostępność może jeszcze spaść |
| L3 | **Liczebność grup szacowana przed zapisami.** | Grupa po zapisach może nie zmieścić się w żadnej sali (`pojemnosc-sali`). | Zdarzenie `groupSizeChanged` aktualizuje liczebność, a #10 od razu zgłasza `brak-sali`. | ile zajęć traci salę, gdy grupy urosną o +g (domyślnie 10%) |
| L4 | **Brakująca albo zła pojemność sali.** | Sala bez poprawnej pojemności jest odrzucana przy imporcie, więc jest nieużywalna. Sala z wątpliwą pojemnością grozi przepełnieniem. | Import zgłasza problem i nie zgaduje pojemności. Generator może ją wylosować dla danych testowych. | sale odrzucone i oznaczone z powodu pola `capacity` |
| L5 | **Czyszczenie opiera się na progach** (#9): wartość > 4 × mediana albo budynek > 0,1° od pozostałych. | Prawdziwa nietypowa wartość może być uznana za błąd, a błąd w normie może przejść. | Wątpliwa wartość zostaje w bazie oznaczona (`flagged`), a nie jest po cichu poprawiana. Progi są w `Thresholds`. | wartości oznaczone jako wątpliwe, które zostały w bazie |
| L6 | **Baza trzyma jeden stan bez historii.** | Zdarzenie zmienia stan od razu, także gdy obowiązuje dopiero za kilka dni, więc plan na bieżący tydzień widzi przyszłą zmianę. | Data obowiązywania jest w dzienniku zdarzeń (`raw`). | ile zastosowanych zdarzeń obowiązuje później, niż zmienia stan, i o ile dni |
| L7 | **Literówka w odwołaniu, która trafia w inny istniejący id, jest niewykrywalna.** | Rekord jest poprawny, tylko wskazuje coś innego, np. grupę z innego programu. | Raport wykonalności zwykle wyłapuje skutek, np. niespójny program grupy. | „niewykrywalne z zasady” w wykrywalności |
| L8 | **Typy i pojemności sal to dane testowe** (#6: układ prawdziwy, reszta wymyślona). | Wyniki bilansu sal i obłożenia są orientacyjne. | Pliki wystarczy podmienić na prawdziwe, bez zmian w kodzie. | — (cecha danych, a nie błąd) |
| L9 | **Raport wykonalności sprawdza tylko warunki konieczne** (#10). | Brak przeszkód nie dowodzi, że plan istnieje. | Ostateczna odpowiedź przychodzi z solvera (#17). | werdykt i przyczyny przeszkód w raporcie jakości |
| L10 | **Po imporcie zostaje tylko id odrzuconego rekordu, nie jego treść.** | Gdy przeszkoda wynika z braku odrzuconego prowadzącego albo sali, nie da się wskazać jej przyczyny na pewno. | Raport podaje odrzucone rekordy tego zasobu jako przyczynę prawdopodobną. | przyczyna „prawdopodobnie odrzucone rekordy” |
| L11 | **Czasy zdarzeń są lokalne, bez strefy.** | Przy zmianie czasu (25.10.2026, 3:00 → 2:00) godzina między 2:00 a 3:00 jest dwuznaczna, więc kolejność zdarzeń z tej godziny może być błędna. | Zdarzenia przychodzą w godzinach pracy (7–20), więc w praktyce to nie występuje. Czas ze strefą jest błędem schematu (#8). | — |
| L12 | **Krótkie zmiany nie zmieniają tygodniowego planu.** | Blokada sali na tydzień albo choroba na trzy dni trafia do terminarza i raportu, a nie do solvera. Plan tygodniowy jej nie omija. | To decyzja projektowa ([architektura](../architektura.html)). Przy długich zmianach plan powstaje od nowa. | — |

## Raport jakości

`krok-quality` zapisuje `_quality.json` i `quality.md` (do repo, PR albo sprawozdania) oraz wypisuje podsumowanie w terminalu.

Źródła danych:
- `--db`: baza po `krok-db import` i `krok-db events`;
- `--data`: katalog z danymi, importowany do tymczasowej bazy, więc `data/krok.db` się nie zmienia. Opcjonalnie dochodzi `--events` ze strumieniem zdarzeń.

Założenia o niepewności to dane wejściowe, a nie stałe w kodzie: `--coordinate-error-m`, `--group-growth`, `--availability-margin`.

**Kategorie problemów.** Problemy z importu i ze zdarzeń są pogrupowane w kategorie, z liczbami per rodzaj i przykładami:

| Kategoria | Rodzaje (`kind` z #9) |
|---|---|
| Braki | `missingField`, `missingFile` |
| Wartości niepewne | każdy problem z `action: flagged` (rekord w bazie, wartość wątpliwa) i zdarzenie zastosowane mimo problemu (`outOfOrder`) |
| Błędy | `keyTypo`, `unknownField`, `invalidValue`, `outlier` (odrzucony), `badReference`, `unknownType`, `malformedJson` |
| Duplikaty | `exactDuplicate`, `nearDuplicate`, `duplicateId`, `duplicateOverride`, powtórzone zdarzenie |
| Skutki odrzuceń | `cascade` oraz zdarzenie, które wskazuje rekord odrzucony przy imporcie |

**Wykrywalność.** Gdy obok danych są manifesty generatora (`_noise.json` z #7, `_events.json` z #8), raport liczy **wykryte / wprowadzone**, tak jak scenariusz „Dane z błędami” w [kryteriach](../etap1/kryteria.md#scenariusze-testowe). Z mianownika wypadają usterki, których nie da się wykryć:
- **niewykrywalne z zasady** (L7);
- **zasłonięte przez skutek importu:** zdarzenie z celową usterką, np. cofnięte w czasie, wskazuje rekord już odrzucony z powodu szumu. Baza i tak je odrzuca, tylko z innego powodu.

Problemy spoza manifestu raport liczy osobno. Kaskady i rekordy zależne od rekordu zmienionego szumem to skutki, a pozostałe to kandydaci na fałszywe alarmy czyszczenia.

**Przyczyny przeszkód.** Każdy błąd z raportu wykonalności (#10) raport przypisuje do jednej przyczyny:
- rekord z problemem z importu;
- rekord zmieniony zdarzeniem;
- prawdopodobnie odrzucone rekordy prowadzących lub sal (L10);
- przyczyna nieznana.

Rekordy są dobierane do rodzaju przeszkody: przy `brak-sali` liczy się grupa, przy obsadzie przedmiot i prowadzący, przy planie studenta grupa i jej przodkowie.

**Wrażliwość.** Dla ograniczeń L1–L6 raport podaje miary z kolumny „Miara” w katalogu.

## Wyniki na danych testowych

Pełny łańcuch: generator (#7) z szumem → import (#9) → zdarzenia (#8) z czystych danych o tym samym ziarnie → raport jakości. 47 przebiegów: `mini` i `kierunek` po 20 ziaren, `wydzial` 7 ziaren.

| Miara | Wynik |
|---|---|
| Szum: wykryte / wprowadzone | **2062 / 2062 (100%)**, niewykrywalnych z zasady 0 |
| Zdarzenia: wykryte / wprowadzone | **368 / 368 (100%)**, a 3 usterki zasłonięte przez skutek importu |
| Problemy spoza manifestu, które nie są skutkami (fałszywe alarmy) | **0** |
| Przeszkody wykonalności bez znanej przyczyny | **0** z 200 (80 z importu, 113 ze zdarzeń, 7 prawdopodobnie z odrzuconych rekordów) |

Przykład dla `kierunek`, ziarno 42, dane z szumem i ze zdarzeniami:

| | Wynik |
|---|---|
| Import | braki 17, wartości niepewne 2, błędy 30, duplikaty 7, skutki odrzuceń 46 |
| Zdarzenia | braki 3, błędy 1, skutki odrzuceń 5 (zdarzenia wskazujące rekordy odrzucone przy imporcie) |
| Wykrywalność | szum 56 / 56, zdarzenia 4 / 4 |
| Wykonalność | 8 przeszkód: 4 z importu (np. grupa z liczebnością 210 oznaczona jako odstająca, z pul usunięto odrzuconych prowadzących), 4 ze zdarzeń (laboratoria urosły do 17–18 osób przy salach na 16 miejsc) |
| L1 | błąd czasu przejścia ±0,94 min przy ±30 m. Przejścia są krótkie (≤ 7,2 min), więc 12 z 15 par ma błąd względny ≥ 20%, choć w minutach to mniej niż minuta |
| L2 | żadna kompetencja nie przekracza 80% dostępności. Najbliżej jest język angielski (72%), który może stracić jeszcze 28% dostępności |
| L3 | **28 zajęć w 11 grupach traci salę przy +10% studentów** (laboratoria po 15 osób przy salach na 16 miejsc). To najsilniejsza zależność planu od niepewnych danych |
| L4 | 3 sale odrzucone przez brak lub złą pojemność |
| L6 | 55 z 70 zastosowanych zdarzeń obowiązuje później, niż zmienia stan, do 17 dni |

Na tych samych danych bez szumu raport nie ma problemów z importu, a wszystkie 4 przeszkody wynikają ze zdarzeń.

Testy (`tests/test_quality.py`) sprawdzają:
- wykrywalność 100% i brak fałszywych alarmów na kilku presetach i ziarnach;
- znaną przyczynę każdej przeszkody;
- kategorie, miary wrażliwości;
- CLI.
