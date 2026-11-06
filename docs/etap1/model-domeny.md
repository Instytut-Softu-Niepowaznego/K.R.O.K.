# Model domeny oraz specyfikacja wejść i wyjść

> Szkic do #1. Wspólna definicja tego, czym operuje system: od niej zależą generator (#7), dane budynków (#6), baza (#9), percepcja (#10, #11) i kryteria (#3).

## Zakres

Modelujemy jeden wydział: Wydział Informatyki (WJ) Politechniki Opolskiej, budynki P1–P6. Najpierw test na jednym kierunku, potem pełne testy całego wydziału. Dane, których nie znamy, są generowane.

- Model zachowuje `facultyId` i `facultyIds`, żeby dało się później rozszerzyć go na kolejne wydziały. Cała uczelnia to opcjonalne rozszerzenie po Sprincie 3.
- Prowadzący spoza wydziału (np. matematyka, fizyka) to zwykli prowadzący z ograniczoną dostępnością.
- Zajętość zasobu przez kogoś z zewnątrz zapisujemy jako `resourceBlock` albo ograniczoną dostępność.
- P6 należy do ogólnouczelnianego centrum językowego, ale stoi na naszym wydziale. Jest w danych jak pozostałe budynki, zajętość przez inne wydziały to `resourceBlock`.
- Języki obce to zwykłe zajęcia. Termin zależy od dostępności prowadzącego, tak jak przy prowadzących spoza wydziału.

## Zasady ogólne

- Klucze JSON po angielsku w `camelCase`, opisy po polsku.
- Każda encja ma stabilne `id` (string). Odwołania tylko przez `id`.
- Długość zajęć podawana w **jednostkach** (45 min). Godziny startu i przerwy są w formacie `HH:MM` i w minutach.
- Wszystkie reguły, które mogą się zmienić (godziny, przerwy, dni, prędkość przejścia), siedzą w konfiguracji, nie w kodzie.
- Obecny kod (`src/krok/harness/plan.py`, `validate_plan`) i dane w `src/krok/data/` używają starego modelu i nie realizują tej specyfikacji, np. nie sprawdzają kolizji w hierarchii grup. Ta specyfikacja obowiązuje dla nowego kodu.

## Zasady układania

Zasady to dane wejściowe, a nie stałe w kodzie. Cykl pracy jest prosty: wprowadzamy zasady, system układa plan, a gdy coś się nie podoba albo zmieni, zmieniamy zasady i układamy plan od nowa.

Do zasad należą godziny i przerwy (`timeGrid`) oraz lista reguł w `config.json`:

```json
{
  "rules": [
    { "id": "max-jednostek-dziennie", "enabled": true, "kind": "hard", "params": { "maxUnits": 10 } },
    { "id": "okienka-studentow", "enabled": true, "kind": "soft", "weight": 1, "params": { "toleranceMinutes": 20 } },
    { "id": "przejscia-miedzy-budynkami", "enabled": true, "kind": "soft", "weight": 15, "params": { "maxPerDay": 2 } }
  ]
}
```

- `kind`: `hard` = plan nie może jej złamać, `soft` = system minimalizuje naruszenia zgodnie z wagą `weight`.
- Włączenie, wyłączenie, zmiana parametrów i wagi nie wymagają zmian w kodzie. Nowy rodzaj reguły wymaga dopisania jego obsługi.
- Opcjonalny `target` to wartość docelowa reguły, którą walidator sprawdza automatycznie.
- Pełna lista reguł z parametrami, wagami, wartościami docelowymi i sposobem pomiaru jest w [kryteriach](kryteria.md) (#3).
- Każdy plan zapisuje kopię zasad, z którymi powstał (`rulesSnapshot`), żeby dało się porównywać wersje i odczytać stary plan po zmianie konfiguracji.

## Czas

### Czas zajęć

Nie ma sztywnej siatki slotów. Zajęcia mają dowolną godzinę startu, a przerwy wynikają z reguł w konfiguracji.

- Jednostka = 45 min (najkrótsze zajęcia). Zajęcia mają liczbę jednostek, a czas trwania jest z niej wyliczany.
- Przerwa w środku zajęć to `innerMinutes`. Przykład: 2 jednostki = 45 + 10 + 45 = 1 h 40 min, czyli 7:30–9:10.
- Przerwa między zajęciami to co najmniej `minBetweenClassesMinutes`. Przykład: 7:30–8:15, kolejne zajęcia od 8:20. Może być dłuższa, jeśli tak ułoży solver.
- Plan to jeden powtarzalny tydzień, opcjonalnie z tygodniami A/B.

Solver dostaje start każdych zajęć jako liczbę minut od początku dnia, a kolizje liczy z nakładania się przedziałów czasu. Zmiana przerw to tylko zmiana konfiguracji.

```json
{
  "timeGrid": {
    "days": ["Pn", "Wt", "Śr", "Cz", "Pt", "So", "Nd"],
    "daysByStudyMode": {
      "full-time": ["Pn", "Wt", "Śr", "Cz", "Pt"],
      "part-time": ["So", "Nd"]
    },
    "dayStart": "07:30",
    "dayEnd": "20:00",
    "unitMinutes": 45,
    "startStepMinutes": 5,
    "allowedStartTimes": null,
    "breaks": {
      "innerMinutes": 10,
      "minBetweenClassesMinutes": 5,
      "overrides": [
        { "from": "16:00", "to": "17:00", "innerMinutes": 5 }
      ]
    }
  }
}
```

- `daysByStudyMode`: studia dzienne (`full-time`) mają zajęcia pn–pt, zaoczne (`part-time`) so–nd. Tryb jest ustawiony w programie studiów.
- `startStepMinutes`: co ile minut mogą zaczynać się zajęcia. 5 min obejmuje i 7:30, i 8:20.
- `allowedStartTimes`: `null` = każda godzina zgodna z krokiem. Różne kierunki i grupy zaczynają o różnych porach, a godzinę startu wyznacza solver. Lista godzin (np. `["07:30", "08:20"]`) ogranicza starty, co też przyspiesza liczenie.
- `breaks.overrides`: inne przerwy w danych godzinach. Reguły można dodawać i zmieniać bez ruszania kodu. Wartości w przykładzie są orientacyjne, przerwy można ustawić dowolnie przed każdym układaniem.
- Grupa, prowadzący i sala muszą mieć między zajęciami co najmniej `minBetweenClassesMinutes`. Przy zmianie budynku przerwa musi też pokryć czas przejścia.

### Parzystość tygodnia

`parity`: `"every"` | `"A"` | `"B"`. Zajęcia `A` i `B` nie kolidują ze sobą. `every` koliduje z oboma.

### Kalendarz semestru i wyjątki

Tygodniowy plan jest rdzeniem. Kalendarz nakłada go na konkretne daty semestru i wprowadza wyjątki. Wyjątki są danymi w `calendar.json`: dodanie dnia wolnego to jeden wpis, bez zmian w kodzie ani w tygodniowym planie.

```json
{
  "calendar": {
    "semesterStart": "2026-10-01",
    "semesterEnd": "2027-01-29",
    "firstWeekParity": "A",
    "partTimeSessions": { "firstWeekend": "2026-10-03", "everyWeeks": 2 },
    "weekParityOverrides": [
      { "weekOf": "2026-12-28", "parity": null }
    ],
    "exceptions": [
      { "id": "swieto-niepodleglosci", "type": "holiday", "from": "2026-11-11", "to": "2026-11-11", "label": "Święto Niepodległości" },
      { "id": "przerwa-swiateczna",    "type": "holiday", "from": "2026-12-22", "to": "2027-01-06", "label": "Przerwa świąteczna" },
      { "id": "rektorskie-1",          "type": "rector",  "from": "2026-11-10", "to": "2026-11-10", "label": "Dzień rektorski" },
      { "id": "rektorskie-godz",       "type": "rector",  "from": "2026-11-18", "to": "2026-11-18", "fromTime": "12:00", "toTime": "20:00",
        "scope": { "facultyIds": ["WJ"] }, "label": "Godziny rektorskie (święto wydziału)" },
      { "id": "odpracowanie",          "type": "dayOverride", "date": "2026-12-15", "followsDay": "Pt", "followsParity": "B",
        "label": "Wtorek z zajęciami jak w piątek B" },
      { "id": "remont-p1-101",         "type": "resourceBlock", "from": "2026-11-02", "to": "2026-11-13",
        "scope": { "roomIds": ["P1/101"] }, "label": "Remont sali" }
    ]
  }
}
```

Typy wyjątków:

| `type` | Znaczenie |
|---|---|
| `holiday` | Dzień lub zakres dni bez zajęć (święta, przerwy). |
| `rector` | Dzień lub godziny rektorskie/dziekańskie. `fromTime`/`toTime` opcjonalne, brak = cały dzień. |
| `dayOverride` | W danym dniu obowiązuje plan innego dnia tygodnia (i opcjonalnie innej parzystości). |
| `resourceBlock` | Niedostępność konkretnej sali, prowadzącego lub grupy w danym okresie. |

- `scope` opcjonalne. Brak = wszystkie dane. Można zawęzić do `facultyIds`, `groupIds` (z potomkami), `roomIds`, `lecturerIds`.
- `partTimeSessions`: zjazdy studiów zaocznych, domyślnie co 2 tygodnie. Układamy jeden powtarzalny weekend, a kalendarz nakłada go na daty zjazdów. Różne zajęcia na różnych zjazdach na razie pomijamy.
- `weekParityOverrides`: ręczne ustawienie parzystości tygodnia albo `null`, żeby tydzień nie liczył się do naprzemienności A/B.
- `holiday` i `rector` nie zmieniają tygodniowego planu. Wpływają na wyliczenie konkretnych terminów i na raport: ile spotkań każde zajęcia faktycznie mają w semestrze i czy któremuś brakuje.
- `resourceBlock` krótszy niż cały semestr też nie zmienia tygodniowego planu. Raport wskazuje spotkania, które trzeba przenieść (zastępstwo lub zmiana sali). Blokada na cały semestr działa jak brak dostępności i trafia do solvera.

Gdy wyjątki się nakładają, kolejność rozstrzygania dla każdej daty jest stała:

1. `dayOverride` określa, z którego dnia brać plan i opcjonalnie którą parzystość zastosować. Dwa takie wpisy na tę samą datę to błąd danych wejściowych.
2. Do każdego spotkania zbieramy wszystkie wyjątki, które obejmują je zakresem i czasem.
3. Status spotkania: `holiday` lub `rector` → `cancelled`, w przeciwnym razie `resourceBlock` → `needsMove`, w przeciwnym razie `held`.

Odpracowanie w dniu wolnym nie przywraca więc zajęć: spotkania są odwołane, a raport pokazuje to jako ostrzeżenie.

## Struktura studentów

Drzewo grup:

```
wydział → kierunek → stopień → rocznik/semestr → grupa wykładowa → grupa ćwiczeniowa → grupa laboratoryjna
```

- Stopnie: `inz` (3,5–4 lata), `lic` (3 lata), `mgr` (1,5–3 lata).
- **Reguła kolizji:** grupa koliduje z każdym swoim przodkiem i potomkiem. Lab podgrupy nie może się nakładać na wykład całego rocznika. Grupy rodzeństwa (L1 i L2) mogą mieć zajęcia równolegle.

```json
{
  "faculties": [{ "id": "WJ", "name": "Wydział Informatyki" }],
  "fields": [{ "id": "WJ-INF", "facultyId": "WJ", "name": "Informatyka" }],
  "programs": [{ "id": "WJ-INF-inz", "fieldId": "WJ-INF", "degree": "inz", "studyMode": "full-time", "semesters": 7 }],
  "groups": [
    { "id": "WJ-INF-inz-s3",      "programId": "WJ-INF-inz", "parentId": null,              "kind": "cohort",   "semester": 3, "size": 180 },
    { "id": "WJ-INF-inz-s3-W1",   "programId": "WJ-INF-inz", "parentId": "WJ-INF-inz-s3",    "kind": "lecture",  "size": 90 },
    { "id": "WJ-INF-inz-s3-C1",   "programId": "WJ-INF-inz", "parentId": "WJ-INF-inz-s3-W1", "kind": "exercise", "size": 30 },
    { "id": "WJ-INF-inz-s3-L1",   "programId": "WJ-INF-inz", "parentId": "WJ-INF-inz-s3-C1", "kind": "lab",      "size": 15 }
  ]
}
```

`kind`: `cohort` | `lecture` | `exercise` | `lab` | `project`.

## Zasoby

Budynki, sale i prowadzący mają pole `facultyIds`. Na razie zawiera jeden wydział, lista zostaje na potrzeby późniejszego rozszerzenia.

### Budynek

```json
{ "id": "P1", "name": "P1", "lat": 50.67, "lon": 17.92, "facultyIds": ["WJ"] }
```

Współrzędne i numery sal w przykładach są orientacyjne, prawdziwe wartości powstają w #6.

Czas przejścia między budynkami liczony ze współrzędnych (odległość / `walkingSpeedKmh` z konfiguracji). Przejście musi zmieścić się w przerwie między zajęciami, inaczej jest to naruszenie.

### Sala

```json
{ "id": "P1/101", "buildingId": "P1", "number": "101", "type": "lab-it", "capacity": 16, "facultyIds": ["WJ"] }
```

- `id` = `buildingId` + `/` + `number` (unikalne globalnie).
- `type`: `lecture-hall` | `classroom` | `lab-it` | `lab-electronics` | … (słownik w konfiguracji).

### Prowadzący

```json
{
  "id": "P-0042",
  "name": "dr Jan Kowalski",
  "facultyIds": ["WJ"],
  "competences": ["programowanie", "bazy-danych"],
  "homeBuildingId": "P1",
  "availability": [
    { "day": "Pn", "from": "07:30", "to": "16:00" },
    { "day": "Śr", "from": "09:15", "to": "20:00" }
  ],
  "maxUnitsWeekly": 20
}
```

`availability` w godzinach. Zajęcia muszą się w całości zmieścić w zakresie. Brak wpisu dla dnia = niedostępny.

## Przedmioty i zajęcia do zaplanowania

### Przedmiot

```json
{
  "id": "BD",
  "name": "Bazy danych",
  "competence": "bazy-danych",
  "lecturerPools": {
    "W": ["P-0042"],
    "C": ["P-0042", "P-0051", "P-0077"],
    "L": ["P-0051", "P-0077", "P-0090"]
  }
}
```

- `lecturerPools` (opcjonalne): obsada przedmiotu, czyli lista prowadzących osobno dla każdej formy. Klucze to formy z `form` zajęć. Brak pola lub brak klucza dla formy = pula to wszyscy prowadzący z kompetencją `competence`.
- Każdy prowadzący z puli musi mieć kompetencję `competence`. Pusta lista albo prowadzący bez kompetencji w puli to błąd danych wejściowych.
- Pulę podajemy na poziomie przedmiotu, a nie zajęć: zmiana obsady to jedna edycja, niezależnie od liczby grup.

### Zajęcia (wymaganie)

Jedno wymaganie = jedna forma przedmiotu dla jednej grupy.

```json
{
  "id": "BD-L-WJ-INF-inz-s3-L1",
  "subjectId": "BD",
  "form": "L",
  "groupId": "WJ-INF-inz-s3-L1",
  "unitsPerWeek": 2,
  "unitsPerMeeting": 2,
  "parity": "every",
  "roomType": "lab-it",
  "lecturerId": null
}
```

- `form`: `W` | `C` | `L` | `P`.
- `unitsPerWeek` / `unitsPerMeeting` = liczba spotkań w tygodniu. Przykład: 4 / 2 = dwa spotkania po 1 h 40 min. Obie wartości to liczby całkowite ≥ 1, a `unitsPerWeek` musi być podzielne przez `unitsPerMeeting`. Inaczej to błąd danych wejściowych.
- `lecturerId`: podane `id` = przydział na sztywno. `null` = solver dobiera prowadzącego, kolejno:
  1. z `subject.lecturerPools[form]`, jeśli przedmiot ma pulę dla tej formy,
  2. w przeciwnym razie spośród wszystkich prowadzących z kompetencją `subject.competence`.
- Podany `lecturerId` musi należeć do puli formy, jeśli pula istnieje. Inaczej to błąd danych wejściowych.
- Wszystkie spotkania jednych zajęć prowadzi ta sama osoba. Przykład: ćwiczenia z BD dla grup C1, C2 i C3 to troje zajęć. Solver może przydzielić im różne osoby z puli, ale każda grupa ma jednego prowadzącego przez cały semestr.
- Sala musi mieć `type` równy `roomType` i `capacity` ≥ `size` grupy. To twarde ograniczenie. Jeśli żadna sala go nie spełnia, solver nie przydziela zajęć, a raport zgłasza naruszenie z `classIds` tych zajęć.

## Wejścia (podsumowanie)

| Plik | Zawartość | Źródło |
|---|---|---|
| `config.json` | `timeGrid`, słownik typów sal, `walkingSpeedKmh` | ręcznie |
| `calendar.json` | semestr, parzystość tygodni, wyjątki | ręcznie lub generator |
| `faculties.json` | wydziały, kierunki, programy | generator |
| `groups.json` | drzewo grup | generator |
| `buildings.json` | budynki ze współrzędnymi | ręcznie (#6) |
| `rooms.json` | sale | ręcznie (#6), pojemność może być generowana |
| `lecturers.json` | prowadzący | generator (dostępność, limity) |
| `subjects.json` | przedmioty z pulami prowadzących | generator |
| `classes.json` | zajęcia do zaplanowania | generator |

### Generator (#7)

Generator wypełnia wszystkie encje tego modelu, razem z kalendarzem, wyjątkami i studiami zaocznymi. Budynki i sale bierze z #6. Oprócz typowych danych generuje przypadki brzegowe, np.:

- grupa większa od każdej sali danego typu
- przedmiot bez prowadzącego z odpowiednią kompetencją
- pula prowadzących, w której nikt nie jest dostępny w godzinach zajęć albo której łączny limit godzin nie wystarcza na wszystkie grupy
- prowadzący bez dostępności albo z limitem niższym od przydzielonych zajęć
- zajęcia 1-jednostkowe, zajęcia A/B i prowadzący uczący na studiach dziennych i zaocznych
- zajęcia dłuższe niż najdłuższy ciągły zakres dostępności prowadzącego
- wyjątki nakładające się na siebie i odpracowanie wypadające w dniu wolnym

## Wyjścia

### Plan

Lista przydziałów. Jeden przydział = jedno spotkanie.

```json
{
  "planId": "plan-2026-11-01-01",
  "rulesSnapshot": { "timeGrid": { "...": "..." }, "rules": [], "walkingSpeedKmh": 5 },
  "assignments": [
    {
      "id": "BD-L-WJ-INF-inz-s3-L1#1",
      "classId": "BD-L-WJ-INF-inz-s3-L1",
      "groupId": "WJ-INF-inz-s3-L1",
      "lecturerId": "P-0042",
      "roomId": "P1/101",
      "day": "Pn",
      "start": "08:20",
      "units": 2,
      "parity": "every"
    }
  ]
}
```

- `id` przydziału = `classId` + `#` + numer spotkania w tygodniu, więc nie zmienia się po ponownym ułożeniu planu.
- `rulesSnapshot` to pełna kopia `timeGrid`, `rules` i `walkingSpeedKmh` z chwili układania, czyli wszystkiego, od czego zależą godziny i czasy przejść.
- Koniec zajęć jest wyliczany z `start`, `units` i przerw z `rulesSnapshot`, nie jest przechowywany.

### Raport

Wartości metryk z [kryteriów](kryteria.md) (#3) (twarde naruszenia, okienka, przejścia itd.) plus lista naruszeń:

```json
{
  "ruleId": "pojemnosc-sali",
  "kind": "hard",
  "message": "Żadna sala typu lab-it nie mieści grupy",
  "assignmentIds": [],
  "classIds": ["BD-L-WJ-INF-inz-s3-L1"]
}
```

Definicje metryk, `ruleId` i pełny format raportu (metryki, wydajność) są w [kryteriach](kryteria.md#raport).

### Widoki

Ten sam plan filtrowany per grupa (razem z przodkami), per prowadzący, per sala.

### Terminarz

Plan rozwinięty na konkretne daty po zastosowaniu kalendarza: lista `{ planId, assignmentId, date, status, exceptionIds }`.

- `status`: `held` | `cancelled` | `needsMove`, zgodnie z kolejnością z sekcji o wyjątkach.
- `exceptionIds`: `id` wszystkich wyjątków obejmujących spotkanie, nie tylko tego, który zdecydował o statusie.
