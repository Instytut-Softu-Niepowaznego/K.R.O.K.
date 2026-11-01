# 001: Solver CP-SAT jako narzędzie harnessu

- **Status:** przyjęta (25.10.2026), zaktualizowana 1.11.2026 po rezygnacji ze slotów
- **Zadanie:** #5. Implementacja: #17 (Sprint 3)
- **Powiązane:** [model domeny](../etap1/model-domeny.md) (#1), [architektura](../architektura.html) (#4), kryteria (#3)

## Problem

System ma ułożyć tygodniowy plan zajęć Wydziału Informatyki: najpierw dla jednego kierunku, potem dla całego wydziału. Każde zajęcia dostają dzień, godzinę startu, salę i często prowadzącego. Plan nie może łamać reguł twardych (np. kolizji grupy, prowadzącego lub sali), a naruszenia reguł miękkich (np. okienka, przejścia) ma mieć jak najmniejsze.

Liczba możliwych przydziałów rośnie kombinatorycznie, a jedna zmiana może wywołać kolizję w innym miejscu planu. Kod potrafi sprawdzić gotowy plan, ale samo sprawdzanie go nie ułoży.

Zasady często się zmieniają, więc nie mogą być zaszyte w kodzie: zmieniamy je w konfiguracji i układamy plan od nowa.

## Rozważone opcje

Ocena jest jakościowa, opcji nie porównywaliśmy pomiarami.

| | Samo LLM | Sam solver | Hybryda: LLM + solver |
|---|---|---|---|
| Brak kolizji | tylko po sprawdzeniu kodem, przy setkach zajęć poprawki mogą nie zbiegać | gwarantowany w ramach modelu | gwarantowany w ramach modelu |
| Dowód, że planu nie ma | nie | tak, gdy solver skończy w limicie czasu | tak, gdy solver skończy w limicie czasu |
| Polecenia w zwykłym języku | tak | nie | tak |
| Reakcja na zmiany i kompromisy | tak, ale bez gwarancji | tylko po ręcznym przełożeniu na ograniczenia | tak, model dobiera działanie, solver liczy |
| Tłumaczenie wyniku | tak | nie, zwraca same wartości | tak |

## Decyzja

**Hybryda.** Solver CP-SAT z Google OR-Tools jest jednym z narzędzi harnessu (`solve_plan`), a nie osobnym systemem.

| Kto | Rola |
|---|---|
| Model główny | rozumie polecenie, dobiera narzędzia, tłumaczy wynik |
| Model decyzyjny | wybiera między poprawnymi wariantami, ocenia kompromisy |
| Solver CP-SAT | liczy przydział zajęć do godzin i sal |
| Kod (walidacja) | niezależnie sprawdza wynik, także wynik solvera |

CP-SAT wybraliśmy, bo ma wprost przedziały czasu i ograniczenie braku nakładania (`NoOverlap`), czyli to, czego wymaga nasz model czasu. Można go też wywołać z Pythona, w którym powstaje harness.

## Przedziały czasu zamiast slotów

Sztywna siatka slotów nie mieści jednocześnie zajęć 7:30–9:10 i startu o 8:20, dlatego z niej zrezygnowaliśmy. Solver pracuje na przedziałach czasu:

| Element | Zapis w modelu |
|---|---|
| Godzina startu | zmienna w minutach od początku dnia, wartości z `dayStart`, `dayEnd`, `startStepMinutes` i `allowedStartTimes` |
| Czas trwania | wyliczany z liczby jednostek, `unitMinutes` i przerw wewnętrznych |
| Kolizja | nakładanie się przedziałów tej samej grupy, prowadzącego lub sali (`NoOverlap`) |
| Wybór sali lub prowadzącego | przedział opcjonalny dla każdej możliwej opcji, aktywny tylko ten wybrany |
| Przerwa między zajęciami | przedział wydłużony o `minBetweenClassesMinutes` |
| Hierarchia grup | zajęcia grupy nadrzędnej blokują też jej podgrupy |
| Tygodnie A/B | osobne sprawdzenie dla A i B: zajęcia `every` należą do obu, a `A` i `B` się nie spotykają |

Szczegółowy kształt modelu ustala #17.

## Reguły z konfiguracji

Solver nie zawiera wartości reguł w kodzie. Przy każdym uruchomieniu buduje model z `config.json`, a blokady bierze z `calendar.json`:

| Wpis | Efekt w modelu |
|---|---|
| `kind: "hard"` | ograniczenie, którego plan nie może złamać |
| `kind: "soft"` z `weight` | kara z tą wagą w minimalizowanej funkcji celu |
| `enabled: false` | reguła pomijana |
| `params` | parametry ograniczenia lub kary |

Zmiana wartości, wagi lub przełączenie reguły wymaga tylko edycji konfiguracji i ponownego ułożenia planu. Nowy **rodzaj** reguły wymaga dopisania jego obsługi w kodzie. Plan zapisuje `rulesSnapshot`, czyli kopię zasad, z którymi powstał. Docelową listę reguł ustala #3.

## Konsekwencje

Korzyści:

- Reguły twarde są spełnione przez każdy plan zwrócony przez solver, jeśli model poprawnie je odwzorowuje.
- Zmiana przerw, godzin lub wag nie wymaga zmian w kodzie.
- LLM nie musi pilnować kolizji i może zająć się poleceniami, kompromisami i tłumaczeniem wyniku.

Koszty i ryzyka:

- **Rozmiar modelu.** Dowolne starty i krok 5 minut dają dużo możliwych wartości. Możliwe środki zaradcze: `allowedStartTimes`, większy krok, limit czasu, podział na podproblemy.
- **Limit czasu ogranicza gwarancje.** Po jego upływie solver może zwrócić plan bez dowodu optymalności albo nie rozstrzygnąć niczego. Harness musi przypisać każdy status CP-SAT do jednego wyniku:
  - `OPTIMAL`: plan z dowodem optymalności.
  - `FEASIBLE`: plan bez dowodu optymalności.
  - `INFEASIBLE`: dowód braku planu. To jedyny status, który go daje.
  - `UNKNOWN`: brak rozstrzygnięcia, ani planu, ani dowodu jego braku.
  - `MODEL_INVALID`: błąd modelu lub narzędzia, a nie wynik planowania.
- **Czas trwania zależny od startu.** `breaks.overrides` zmieniają przerwę wewnętrzną w danych godzinach, więc długość przedziału zależy od godziny startu. To komplikuje model.
- **Czas przejścia zależny od sal.** Przerwa przy zmianie budynku zależy od wybranych sal, więc stałe wydłużenie przedziału tu nie wystarcza i potrzebne są dodatkowe ograniczenia.
- **Sprzeczne zasady.** Sam wynik „brak planu” nie wyjaśnia przyczyny. Wskazanie sprzecznych ograniczeń to osobna praca w #17.
- **Błąd w modelu.** Solver gwarantuje tylko to, co w nim zapisano, dlatego kod waliduje wynik niezależnie.
- **Nowa zależność.** `ortools` dojdzie do projektu w #17. Obecny kod (`src/krok/harness/plan.py`) używa jeszcze starego modelu czasu.
- **Przeplanowanie.** Po zdarzeniu plan powinien zmienić się jak najmniej, więc model będzie potrzebował kary za odejście od obecnego planu (#17).

## Miejsce w architekturze

Solver jest jednym z narzędzi harnessu. W [diagramie architektury](../architektura.html) odpowiada mu karta 09, którą dodaje PR #23. Przepływ:

1. Model główny wywołuje `solve_plan`.
2. Solver czyta dane z bazy oraz zasady z `config.json` i `calendar.json`.
3. Kod waliduje wynik, a błędy wracają do modelu.
4. Plan razem z `rulesSnapshot` trafia do bazy i na wyjście.
5. Po zmianie zasad lub zdarzeniu z otoczenia plan jest układany ponownie.
