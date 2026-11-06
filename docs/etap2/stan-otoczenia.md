# Stan otoczenia i raport wykonalności (#10)

Percepcja zamienia dane wejściowe na to, czego potrzebuje planowanie, i odpowiada na pytanie: **czy plan w ogóle da się ułożyć?**

```bash
uv run krok-env --data data/generated/wydzial    # raport w terminalu + _feasibility.json i _environment.json
uv run krok-env --preset edge-cases              # dane z presetu w pamięci
```

Kod wyjścia: `0` = brak wykrytych przeszkód, `1` = planu nie da się ułożyć, `2` = złe dane albo argumenty. Dane muszą przechodzić walidację modelu i `reference_errors`. Dane z szumem najpierw przechodzą import i czyszczenie (#9).

```python
from krok.perception import analyze
state, report = analyze(dataset)   # EnvironmentState, FeasibilityReport
```

## Stan otoczenia (`EnvironmentState`)

| Element | Co zawiera |
|---|---|
| `travel` | macierz czasów przejść w minutach: odległość w linii prostej × `routeFactor` / `walkingSpeedKmh` × 60 ([kryteria](../etap1/kryteria.md#pojęcia-używane-w-pomiarach)). Walidator ma używać tej samej funkcji `travel_minutes`. |
| `lecturer_availability` | macierz dostępności: prowadzący → dzień → przedziały minut, przycięte do `dayStart`–`dayEnd`, po odjęciu blokad na cały semestr |
| `room_availability`, `rooms_by_type` | dostępność sal i sale pogrupowane po typie, od największej (`rooms_by_capacity()`: typ → pojemność → liczba sal) |
| `classes` | dla każdych zajęć: tryb studiów, najkrótszy czas spotkania, kto może je prowadzić (`eligible`: przydział na sztywno, pula albo kompetencja) i komu mieszczą się w dostępności (`available`) |
| `leaf_groups`, `ancestors` | grupy-liście i ich przodkowie, czyli plan studenta z kryteriów |

`state.export()` zapisuje to do `_environment.json`.

Blokady (`resourceBlock`) na cały semestr działają jak brak dostępności, zgodnie z modelem domeny. Krótsze blokady nie zmieniają tygodniowego planu, więc tu ich nie uwzględniamy.

## Raport wykonalności

**Każde sprawdzenie to warunek konieczny.** Problem `error` oznacza, że żaden plan nie spełni reguły twardej `ruleId`, więc `verdict: "infeasible"` jest pewny. Brak błędów (`"no-obstacles-found"`) nie dowodzi, że plan istnieje, bo rozstrzyga to dopiero solver (#17). Żeby nie było fałszywych alarmów, percepcja przyjmuje najkorzystniejsze założenia: najkrótszą przerwę w środku zajęć (z `breaks.overrides`) i dowolne rozłożenie zajęć w dostępnym czasie.

Każde sprawdzenie zakłada, że obowiązują pewne reguły twarde. Jeśli któraś z nich jest wyłączona (`enabled: false`), plan może ją łamać, więc problem staje się ostrzeżeniem. Na przykład bez `komplet-godzin` plan może pominąć zajęcia, a wtedy żaden brak sali nie jest przeszkodą.

| `code` | `ruleId` | Kiedy |
|---|---|---|
| `brak-sali` | `pojemnosc-sali` | żadna sala typu `roomType` nie mieści grupy. Bez reguły `typ-sali` liczy się każda sala |
| `brak-kompetencji` | `kompetencja-prowadzacego` | nikt nie ma kompetencji przedmiotu |
| `przydzial-bez-kompetencji` | `kompetencja-prowadzacego` | `lecturerId` na sztywno bez kompetencji przedmiotu |
| `brak-dostepnego-prowadzacego` | `dostepnosc-prowadzacego` | nikomu z możliwych prowadzących spotkanie nie mieści się w żadnym oknie dostępności (z krokiem `startStepMinutes` i `allowedStartTimes`) w dniach trybu studiów |
| `za-malo-czasu-prowadzacego` | `dostepnosc-prowadzacego` | zajęcia przydzielone na sztywno trwają dłużej niż cała dostępność prowadzącego |
| `za-malo-czasu-puli` | `dostepnosc-prowadzacego` | zajęcia z pulą trwają dłużej niż łączna dostępność puli |
| `za-malo-czasu-kompetencji` | `dostepnosc-prowadzacego` | zajęcia z kompetencją trwają dłużej niż łączna dostępność prowadzących z tą kompetencją |
| `limit-prowadzacego` | `limit-godzin-prowadzacego` | przydziały na sztywno przekraczają `maxUnitsWeekly` w tygodniu A lub B |
| `limit-puli` | `limit-godzin-prowadzacego` | zajęcia z pulą przekraczają łączny limit puli |
| `limit-kompetencji` | `limit-godzin-prowadzacego` | zajęcia z kompetencją przekraczają sumę limitów prowadzących z tą kompetencją |
| `za-malo-sal` | `komplet-godzin` | czas zajęć sal danego typu, trybu i progu pojemności (grupy ≥ k osób → sale ≥ k miejsc) przekracza dostępny czas tych sal |
| `za-malo-czasu-grupy` | `komplet-godzin` | plan studenta wymaga więcej czasu, niż dają dni zajęć |
| `za-duzo-jednostek-grupy` | `max-jednostek-dziennie` | plan studenta ma więcej jednostek niż `maxUnits` × liczba dni |
| `spotkanie-ponad-limit-dzienny` | `max-jednostek-dziennie` | jedno spotkanie ma więcej jednostek niż `maxUnits` |
| `zajecia-dluzsze-niz-dzien` | `godziny-dnia` | spotkanie dłuższe niż `dayStart`–`dayEnd` |
| `zablokowana-grupa` | `blokady-zasobow` | grupa (lub jej przodek) ma blokadę na cały semestr, a ma zajęcia |

Ostrzeżenia (nie przeszkody): `wysokie-oblozenie-sal` (> 85%), `wysokie-obciazenie-kompetencji` (> 90% limitów), `prowadzacy-bez-dostepnosci`, `odpracowanie-w-dniu-wolnym` (`dayOverride` w dniu wolnym, zajęcia i tak będą odwołane).

Limity i czasy liczymy osobno dla tygodnia A (`every` + `A`) i B (`every` + `B`), tak jak w kryteriach, i bierzemy gorszy tydzień.

`balance` w raporcie to bilans zasobów:
- `rooms`: czas sal per typ i tryb, potrzebny vs dostępny;
- `competences`: jednostki i minuty potrzebne vs suma limitów i dostępności prowadzących;
- `groups`: 20 najciaśniejszych planów studenta.

## Testy i wydajność

- Na presecie `edge-cases` każdy przypadek brzegowy, który jest przeszkodą, trafia do raportu z właściwym `code` i id, na każdym z 40 ziaren. Przypadki, które przeszkodą nie są (zajęcia 1-jednostkowe, A/B, prowadzący na dziennych i zaocznych), nie dają błędów.
- Na presetach `mini`, `kierunek` i `wydzial` (40 ziaren) nie ma błędów.
- Każde sprawdzenie ma też test na celowo zepsutych danych.
- Wydajność: `wydzial` (≈ 700 zajęć) liczy się w ≈ 0,01–0,04 s.
