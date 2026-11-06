# Kryteria działania i metryki jakości planu

> Szkic do #3. Po czym poznamy, że system działa poprawnie. Każde kryterium jest regułą w `config.json` i każde da się zmierzyć automatycznie. Encje i formaty są w [modelu domeny](model-domeny.md) (#1), cel systemu w [opisie problemu](problem-i-otoczenie.md) (#2).

## Zasady

- **Kryterium = reguła w konfiguracji.** Każda reguła ma `id`, `kind` (`hard` lub `soft`), `enabled`, opcjonalne `params`, dla miękkich `weight` i opcjonalny `target`. Format jest opisany w [modelu domeny](model-domeny.md#zasady-układania).
- **Plan poprawny** to plan bez żadnego naruszenia włączonej reguły twardej. Solver może zwrócić plan częściowy (np. gdy dla zajęć nie ma sali), ale walidacja oznacza go wtedy jako niepoprawny, a raport wskazuje naruszenia.
- **Jakość planu** to wartości reguł miękkich. Solver minimalizuje sumę `weight × wartość` po włączonych regułach miękkich.
- **Mierzy kod, nie model.** Wszystkie wartości liczy walidator z planu, danych i `rulesSnapshot`, niezależnie od solvera. Dzięki temu da się porównać plany ułożone przy różnych zasadach.
- **Wartości docelowe też są danymi.** Wartość docelowa reguły to jej `target` w konfiguracji, który walidator sprawdza automatycznie. Reguły twarde bez `target` mają cel 0 naruszeń. Wartości z tego dokumentu są początkowe: kalibrujemy je po pierwszych uruchomieniach na presecie `kierunek` (#7).

### Pojęcia używane w pomiarach

- **Plan studenta.** Liczymy go dla każdej grupy-liścia (grupy bez podgrup): to zajęcia tej grupy i wszystkich jej przodków. Metryki studentów liczymy po grupach-liściach, bo każda odpowiada konkretnym studentom.
- **Tydzień A i B.** Jeśli w planie są zajęcia `A` lub `B`, metryki liczymy osobno dla tygodnia A (zajęcia `every` + `A`) i B (`every` + `B`). Raport podaje obie wartości, a wartość docelowa dotyczy gorszej. Bez zajęć A/B jest jeden tydzień.
- **Dni zajęć.** Dni z `timeGrid.daysByStudyMode` dla trybu programu grupy.
- **Koniec zajęć** wyliczamy ze `start`, `units` i przerw z `rulesSnapshot` (model domeny, sekcja o czasie).
- **Kolejne zajęcia.** Dwa spotkania tego samego planu studenta, prowadzącego lub sali w tym samym dniu, bez innego spotkania pomiędzy.
- **Przerwa** między kolejnymi zajęciami = start drugich − koniec pierwszych, w minutach.
- **Czas przejścia** między budynkami = odległość w linii prostej ze współrzędnych × `routeFactor` / `walkingSpeedKmh`. Macierz czasów liczy percepcja (#10), a walidator liczy ją tym samym wzorem z wartości w `rulesSnapshot`.

## Reguły twarde

Plan z choćby jednym naruszeniem jest niepoprawny. Wartość reguły to liczba naruszeń, a wartość docelowa zawsze wynosi 0.

| `id` | Kryterium | Jak mierzymy (jedno naruszenie to…) | `params` |
|---|---|---|---|
| `spojnosc-planu` | Przydział odwołuje się do istniejących danych i zgadza się z zajęciami | przydział z nieistniejącym `classId`, `groupId`, `lecturerId` lub `roomId`; przydział z `groupId`, `units`, `parity` innymi niż w zajęciach; przydział z `lecturerId` innym niż `lecturerId` zajęć, gdy zajęcia mają go podanego; zajęcia, których spotkania mają różnych prowadzących | — |
| `kolizja-grup` | Grupa nie ma dwóch zajęć naraz, z hierarchią z #1 | para spotkań, które nakładają się w czasie, w tym samym dniu, w zgodnej parzystości, dla tej samej grupy albo grup w relacji przodek–potomek | — |
| `kolizja-prowadzacych` | Prowadzący nie ma dwóch zajęć naraz | para nakładających się spotkań tego samego prowadzącego | — |
| `kolizja-sal` | Sala nie ma dwóch zajęć naraz | para nakładających się spotkań w tej samej sali | — |
| `przerwa-miedzy-zajeciami` | Między zajęciami jest co najmniej minimalna przerwa | kolejne zajęcia grupy (z hierarchią), prowadzącego lub sali z przerwą krótszą niż `minBetweenClassesMinutes` | z `timeGrid.breaks` |
| `czas-przejscia` | Przejście między budynkami mieści się w przerwie | kolejne zajęcia planu studenta lub prowadzącego w różnych budynkach, gdy przerwa < `minBetweenClassesMinutes` + czas przejścia | `walkingSpeedKmh`, `routeFactor` z konfiguracji |
| `typ-sali` | Sala ma wymagany typ | spotkanie w sali o `type` różnym od `roomType` zajęć | — |
| `pojemnosc-sali` | Sala mieści grupę | spotkanie w sali o `capacity` < `size` grupy, a także zajęcia, dla których żadna sala nie ma `capacity` ≥ `size` grupy (`classIds` w raporcie). Gdy `typ-sali` jest włączona, liczą się tylko sale o `type` równym `roomType` zajęć, a gdy wyłączona, każda sala | — |
| `kompetencja-prowadzacego` | Prowadzący może prowadzić te zajęcia | spotkanie prowadzącego bez `competence` przedmiotu albo spoza `lecturerPools` dla formy zajęć, jeśli przedmiot ma pulę | — |
| `dostepnosc-prowadzacego` | Prowadzący uczy tylko w godzinach dostępności | spotkanie, które nie mieści się w całości w jednym zakresie `availability` prowadzącego w tym dniu | — |
| `blokady-zasobow` | Zajęcia omijają blokady na cały semestr | spotkanie w sali, z prowadzącym lub grupą objętą `resourceBlock` trwającym cały semestr | — |
| `limit-godzin-prowadzacego` | Prowadzący nie przekracza limitu | prowadzący, którego suma `units` w tygodniu A lub B przekracza `maxUnitsWeekly` | — |
| `komplet-godzin` | Każde zajęcia mają dokładnie tyle spotkań, ile wymagają | brakujące spotkanie (zajęcia mają mniej przydziałów niż `unitsPerWeek / unitsPerMeeting`) albo nadmiarowe spotkanie (więcej przydziałów niż ta liczba) | — |
| `godziny-dnia` | Zajęcia w dozwolonych dniach i godzinach | spotkanie w dniu spoza `daysByStudyMode` trybu, przed `dayStart`, po `dayEnd` albo ze startem niezgodnym z `startStepMinutes` lub `allowedStartTimes` | z `timeGrid` |
| `max-jednostek-dziennie` | Student nie ma za dużo zajęć jednego dnia | plan studenta w dniu z sumą `units` > `maxUnits` | `maxUnits: 10` |

Jedne zajęcia to jedna forma przedmiotu dla jednej grupy (model domeny), więc ćwiczenia z tego samego przedmiotu dla kilku grup to osobne zajęcia. Każde mogą mieć innego prowadzącego, ale wszystkie spotkania jednych zajęć prowadzi ta sama osoba.

Kolizje (`kolizja-*`) liczymy z nakładania się przedziałów czasu, tak jak solver ([decyzja 001](../decyzje/001-solver.md)). Spotkania `A` i `B` nie kolidują ze sobą, a `every` koliduje z oboma.

## Reguły miękkie

Plan może je naruszać. Solver minimalizuje naruszenia zgodnie z wagą. Wagi w tabeli są wartościami początkowymi.

| `id` | Kryterium | Wartość reguły | Jednostka | `params` | `weight` | `target` |
|---|---|---|---|---|---|---|
| `okienka-studentow` | Mało okienek | suma przerw dłuższych niż `toleranceMinutes` między kolejnymi zajęciami planu studenta, po wszystkich grupach-liściach i dniach | minuty | `toleranceMinutes: 20` | 1 | `meanPerLeafGroup` ≤ 120 (min na grupę-liść w tygodniu) |
| `najdluzsze-okienko` | Brak długich okienek | liczba przerw ≥ `maxGapMinutes` w planach studentów | okienka | `maxGapMinutes: 180` | 60 | `total` = 0 |
| `przejscia-miedzy-budynkami` | Mało przejść | liczba zmian budynku między kolejnymi zajęciami planów studentów i prowadzących | przejścia | `maxPerDay: 2` | 15 | `meanPerLeafGroupDay` ≤ 0,5 oraz `daysOverMaxPerDay` = 0 |
| `budynek-macierzysty` | Prowadzący uczy u siebie | suma `units` spotkań poza `homeBuildingId` prowadzącego | jednostki | — | 5 | `shareOfUnits` ≤ 0,2 |
| `rownomierny-rozklad` | Zajęcia równo w tygodniu | suma po grupach-liściach: największa minus najmniejsza dzienna liczba `units` w dniach zajęć | jednostki | — | 10 | `maxPerLeafGroup` ≤ 4 |
| `pozne-zajecia` | Brak zajęć późnym wieczorem | suma minut spotkań po godzinie `after` dla grup z trybów `studyModes` | minuty | `after: "18:00"`, `studyModes: ["full-time"]` | 2 | `total` = 0 |
| `stabilnosc-planu` | Przeplanowanie zmienia mało | liczba przydziałów, którym zmienił się dzień, start, sala lub prowadzący względem planu bazowego (po `id` przydziału) | przydziały | — | 30 | `changedUnaffected` = 0 |

- Solver minimalizuje sumę `weight × wartość reguły`. Każda reguła ma inną jednostkę, więc waga to kara za jedną jednostkę. Przy wagach z tabeli jedno przejście kosztuje tyle co 15 min okienek, jedno okienko ≥ 3 h tyle co godzina zwykłych okienek, a przeniesienie przydziału przy przeplanowaniu tyle co dwa przejścia. Proporcje ustalamy w kalibracji.
- `target` to lista warunków na statystykach wartości reguły, które liczy walidator:

  | Statystyka | Znaczenie |
  |---|---|
  | `total` | wartość reguły dla całego planu |
  | `meanPerLeafGroup` | `total` / liczba grup-liści (tydzień) |
  | `meanPerLeafGroupDay` | `total` / (grupy-liście × dni zajęć) |
  | `maxPerLeafGroup` | największa wartość dla jednej grupy-liścia |
  | `daysOverMaxPerDay` | liczba dni grupy-liścia lub prowadzącego z wartością > `maxPerDay` |
  | `shareOfUnits` | `total` / suma `units` wszystkich spotkań |
  | `changedUnaffected` | zmienione przydziały, których nie dotyczy zdarzenie (inna grupa, prowadzący i sala niż w zdarzeniu) |

  Warunek ma postać `{ "stat": "...", "max": liczba }`. Reguła spełnia wartość docelową, gdy spełnia wszystkie swoje warunki. Reguły twarde mają domyślnie `total` = 0.
- `stabilnosc-planu` działa tylko przy ponownym planowaniu, gdy harness poda plan bazowy. Przy pierwszym układaniu ma wartość 0.
- `najdluzsze-okienko` liczy się dodatkowo do `okienka-studentow`. To celowe: długie okienko jest gorsze od kilku krótkich o tej samej sumie.

## Reguły w konfiguracji

Pełna lista z wartościami początkowymi. Wartości `timeGrid`, `walkingSpeedKmh` i `routeFactor` są w tym samym `config.json`, a reguły twarde, które z nich korzystają, nie powtarzają ich w `params`.

```json
{
  "walkingSpeedKmh": 5,
  "routeFactor": 1.3,
  "rules": [
    { "id": "spojnosc-planu",             "enabled": true, "kind": "hard" },
    { "id": "kolizja-grup",               "enabled": true, "kind": "hard" },
    { "id": "kolizja-prowadzacych",       "enabled": true, "kind": "hard" },
    { "id": "kolizja-sal",                "enabled": true, "kind": "hard" },
    { "id": "przerwa-miedzy-zajeciami",   "enabled": true, "kind": "hard" },
    { "id": "czas-przejscia",             "enabled": true, "kind": "hard" },
    { "id": "typ-sali",                   "enabled": true, "kind": "hard" },
    { "id": "pojemnosc-sali",             "enabled": true, "kind": "hard" },
    { "id": "kompetencja-prowadzacego",   "enabled": true, "kind": "hard" },
    { "id": "dostepnosc-prowadzacego",    "enabled": true, "kind": "hard" },
    { "id": "blokady-zasobow",            "enabled": true, "kind": "hard" },
    { "id": "limit-godzin-prowadzacego",  "enabled": true, "kind": "hard" },
    { "id": "komplet-godzin",             "enabled": true, "kind": "hard" },
    { "id": "godziny-dnia",               "enabled": true, "kind": "hard" },
    { "id": "max-jednostek-dziennie",     "enabled": true, "kind": "hard", "params": { "maxUnits": 10 } },

    { "id": "okienka-studentow",          "enabled": true, "kind": "soft", "weight": 1,  "params": { "toleranceMinutes": 20 },
      "target": [{ "stat": "meanPerLeafGroup", "max": 120 }] },
    { "id": "najdluzsze-okienko",         "enabled": true, "kind": "soft", "weight": 60, "params": { "maxGapMinutes": 180 },
      "target": [{ "stat": "total", "max": 0 }] },
    { "id": "przejscia-miedzy-budynkami", "enabled": true, "kind": "soft", "weight": 15, "params": { "maxPerDay": 2 },
      "target": [{ "stat": "meanPerLeafGroupDay", "max": 0.5 }, { "stat": "daysOverMaxPerDay", "max": 0 }] },
    { "id": "budynek-macierzysty",        "enabled": true, "kind": "soft", "weight": 5,
      "target": [{ "stat": "shareOfUnits", "max": 0.2 }] },
    { "id": "rownomierny-rozklad",        "enabled": true, "kind": "soft", "weight": 10,
      "target": [{ "stat": "maxPerLeafGroup", "max": 4 }] },
    { "id": "pozne-zajecia",              "enabled": true, "kind": "soft", "weight": 2,  "params": { "after": "18:00", "studyModes": ["full-time"] },
      "target": [{ "stat": "total", "max": 0 }] },
    { "id": "stabilnosc-planu",           "enabled": true, "kind": "soft", "weight": 30,
      "target": [{ "stat": "changedUnaffected", "max": 0 }] }
  ]
}
```

- `id` jest jednocześnie rodzajem reguły: walidator i solver mają obsługę dla każdego `id`. Nieznane `id` to błąd konfiguracji, a nie cicho pomijana reguła.
- `enabled: false` wyłącza regułę w walidatorze i w solverze. Plan ułożony z wyłączoną regułą twardą raport oznacza ostrzeżeniem, bo może łamać podstawowe założenia (np. kolizje).
- Zmiana `kind` (np. `pojemnosc-sali` jako miękka z karą za przepełnienie) wymaga, żeby reguła miała obsługę w obu postaciach. Na start (#17) reguły z sekcji twardych są tylko twarde, a miękkie tylko miękkie. Walidator liczy wartość każdej reguły niezależnie od `kind`, więc raport zawsze ją pokazuje.
- Zmiana wag, parametrów, wartości docelowych i włączenia nie wymaga zmian w kodzie. Plan zapisuje je w `rulesSnapshot`.

## Metryki wydajności

Mierzymy na presetach generatora (#7) z ustalonym ziarnem, na tej samej maszynie.

| Metryka | Jak mierzymy | `mini` | `kierunek` | `wydzial` |
|---|---|---|---|---|
| Czas ułożenia planu | czas ściany wywołania `solve_plan`: budowa modelu + rozwiązanie | ≤ 10 s | ≤ 2 min | ≤ 15 min |
| Status solvera | status CP-SAT z [decyzji 001](../decyzje/001-solver.md) | `OPTIMAL` | `OPTIMAL` lub `FEASIBLE` | `FEASIBLE` |
| Luka optymalności | (wartość celu − najlepsze ograniczenie dolne) / wartość celu, z CP-SAT | 0% | ≤ 5% | ≤ 20% |
| Czas walidacji | czas liczenia wszystkich reguł dla gotowego planu | ≤ 1 s | ≤ 2 s | ≤ 10 s |
| Czas przeplanowania | czas od zdarzenia (#8) do nowego poprawnego planu | — | ≤ 1 min | ≤ 5 min |
| Kroki agenta | liczba odpowiedzi modelu do udanego `save_plan` (limit `MAX_STEPS`) | raportujemy | raportujemy | raportujemy |

- Limit czasu solvera to parametr uruchomienia, a nie stała w kodzie. Gdy go przekroczy, raportujemy status i lukę, a nie tylko czas.
- Czas walidacji ma znaczenie, bo harness waliduje plan po każdej zmianie, także w pętli agenta.

## Scenariusze testowe

| Scenariusz | Dane | Co sprawdzamy | Oczekiwany wynik |
|---|---|---|---|
| Mały zestaw | dane przykładowe, potem preset `mini` | pełny cykl: dane → plan → walidacja → zapis | plan poprawny, `OPTIMAL`, czas z tabeli |
| Jeden kierunek | preset `kierunek` | reguły twarde, wartości reguł miękkich, wydajność | plan poprawny, wartości docelowe reguł miękkich spełnione |
| Cały wydział | preset `wydzial` | skala, limit czasu, luka optymalności | plan poprawny w limicie czasu, raport ze wszystkimi metrykami |
| Przypadki brzegowe | preset `edge-cases` | przypadki z modelu domeny, np. grupa większa od każdej sali, przedmiot bez prowadzącego | każdy przypadek w raporcie z właściwym `ruleId` i `classIds`, system nie przerywa pracy |
| Dane z błędami | dane z szumem (#7): braki, literówki, duplikaty, nieistniejące odwołania | percepcja (#9, #11) | wykryte 100% wprowadzonych błędów znanych typów, import się nie przerywa |
| Zdarzenia | preset `kierunek` + strumień zdarzeń (#8) | przeplanowanie | po każdym zdarzeniu plan poprawny, `stabilnosc-planu` w wartości docelowej |
| Zmiana zasad | preset `kierunek`, np. inne przerwy, `allowedStartTimes`, waga | pętla zasad bez zmian w kodzie | nowy plan zgodny z nowymi zasadami, inny `rulesSnapshot` |
| Sprzeczne zasady | preset `mini` z regułami, których nie da się spełnić naraz | zachowanie przy braku planu | status `INFEASIBLE` i wskazanie sprzecznych reguł zamiast pustego wyniku (#17) |

- Wykrywalność błędów (scenariusz z szumem) liczymy jako: wykryte / wprowadzone. Wymaga to, żeby generator zapisywał listę wprowadzonych błędów obok danych.
- Każdy scenariusz da się uruchomić w `pytest` (#12). Scenariusze z presetem `wydzial` są wolne, więc w CI uruchamiamy je osobno.

## Jak liczymy: walidator

Punktem wyjścia jest obecny `validate` w `src/krok/harness/plan.py` (narzędzie `validate_plan`). Działa na starym modelu (sloty 1 h, nazwy zamiast `id`), więc nowy walidator powstaje na modelu z #1.

| Reguła | Obecny `validate` | Brakuje |
|---|---|---|
| `spojnosc-planu` | sprawdza istnienie kursu, sali, prowadzącego | `id` zamiast nazw, zgodność `units`, `parity` i prowadzącego z zajęciami |
| `kolizja-grup`, `kolizja-prowadzacych`, `kolizja-sal` | ta sama godzina i ta sama nazwa | przedziały czasu, hierarchia grup, parzystość |
| `typ-sali` | jest | — |
| `kompetencja-prowadzacego` | sprawdza kompetencję | pula prowadzących `lecturerPools` |
| `komplet-godzin` | porównuje godziny (brak i nadmiar) | liczenie spotkań po `unitsPerMeeting` |
| `budynek-macierzysty` | ostrzeżenie | wartość liczbowa |
| `przejscia-miedzy-budynkami` | ostrzeżenie dla prowadzącego | plany studentów, wartość liczbowa |
| pozostałe | brak | całe |

Docelowy kształt:

- Każde `id` reguły ma jedną funkcję, która dla planu, danych i `rulesSnapshot` zwraca wartość oraz listę naruszeń. Rejestr funkcji po `id` sprawia, że nowy rodzaj reguły to nowa funkcja, a reszta się nie zmienia.
- Walidator nie korzysta z wyników solvera, tylko z zapisanego planu.
- Wynik trafia do raportu z modelu domeny.

### Raport

Raport z [modelu domeny](model-domeny.md#raport) rozszerzamy o listę metryk i wydajność:

```json
{
  "planId": "plan-2026-11-01-01",
  "valid": false,
  "metrics": [
    { "ruleId": "kolizja-grup",   "kind": "hard", "value": 0, "unit": "naruszenia", "target": [{ "stat": "total", "max": 0 }], "met": true },
    { "ruleId": "pojemnosc-sali", "kind": "hard", "value": 1, "unit": "naruszenia", "target": [{ "stat": "total", "max": 0 }], "met": false },
    { "ruleId": "okienka-studentow", "kind": "soft", "value": 1840, "unit": "minuty",
      "stats": { "total": 1840, "meanPerLeafGroup": 115 },
      "byWeek": { "A": { "total": 1840, "meanPerLeafGroup": 115 }, "B": { "total": 1600, "meanPerLeafGroup": 100 } },
      "target": [{ "stat": "meanPerLeafGroup", "max": 120 }], "met": true }
  ],
  "performance": { "solveSeconds": 48.2, "solverStatus": "FEASIBLE", "optimalityGap": 0.03, "validateSeconds": 0.4 },
  "violations": [
    { "ruleId": "pojemnosc-sali", "kind": "hard", "message": "Żadna sala typu lab-it nie mieści grupy",
      "assignmentIds": [], "classIds": ["BD-L-WJ-INF-inz-s3-L1"] }
  ]
}
```

- `valid` = brak naruszeń włączonych reguł twardych.
- `metrics` ma wpis dla każdej włączonej reguły. `value` i `stats` dotyczą gorszego z tygodni A i B, a `byWeek` z wartościami obu tygodni pojawia się, gdy plan ma tygodnie A/B.
- `target` jest kopią z `rulesSnapshot`, a `met` mówi, czy reguła spełnia wszystkie jego warunki.
