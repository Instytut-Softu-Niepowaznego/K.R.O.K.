# Strumień zdarzeń z otoczenia (#8)

W trakcie semestru świat się zmienia: sale się psują, prowadzący chorują, grupy rosną i maleją. Strumień zdarzeń symuluje te zmiany. Odbiera go percepcja (#9), a o skutku decyduje reszta systemu. Zgodnie z [problemem i otoczeniem](../etap1/problem-i-otoczenie.md#skąd-przychodzą-zmiany-w-trakcie-semestru): trwałe zmiany uruchamiają ponowne planowanie, a krótkie zmieniają tylko terminarz i raport.

```bash
uv run krok-events generate --preset kierunek --seed 42           # dane z krok-gen + events.jsonl w data/generated/kierunek
uv run krok-events generate --data data/generated/wydzial          # zdarzenia do istniejących danych (seed z _meta.json)
uv run krok-events replay data/generated/kierunek/events.jsonl --speed 86400   # 1 dzień symulacji = 1 s
uv run krok-events replay data/generated/kierunek/events.jsonl --errors-only
```

## Format

Plik `events.jsonl`, jedna linia = jedno zdarzenie:

```json
{"id": "E-0002", "timestamp": "2026-09-25T15:20:00", "type": "roomUnavailable",
 "payload": {"roomId": "P1/102", "from": "2026-10-05", "to": "2026-11-15", "fromTime": null, "toTime": null, "reason": "remont"}}
```

- `id`: unikalny identyfikator zdarzenia. Pozwala wykryć duplikaty i odwołać się do zdarzenia w raporcie (np. `changedUnaffected` w [kryteriach](../etap1/kryteria.md)).
- `timestamp`: chwila, w której system dowiaduje się o zmianie (czas lokalny, bez strefy). Linie są posortowane po tym polu.
- `payload`: daty, od kiedy zmiana obowiązuje (`from`/`to` albo `effectiveFrom`). Informacja zwykle przychodzi kilka dni wcześniej (`events.notice_days`).

Schematy to modele pydantic w `src/krok/model/events.py`: `parse_event(linia_albo_słownik)` waliduje jedno zdarzenie.

| `type` | `payload` | Skutek (problem i otoczenie) |
|---|---|---|
| `roomUnavailable` | `roomId`, `from`, `to`, opcjonalnie `fromTime`/`toTime`, `reason` | jak `resourceBlock`: krótka blokada zmienia terminarz, długa trafia do solvera |
| `lecturerAbsent` | `lecturerId`, `from`, `to`, `reason` | krótka: terminarz i raport, długa: ponowne planowanie z zastępstwem |
| `lecturerAvailabilityChanged` | `lecturerId`, `effectiveFrom`, `availability` (nowa, pełna lista przedziałów) | ponowne planowanie |
| `groupSizeChanged` | `groupId`, `size`, `effectiveFrom` | ponowne planowanie, możliwa zmiana sali |
| `groupAdded` | `group` (encja `Group`), `classes` (jej zajęcia, może być pusta lista) | ponowne planowanie |
| `subjectAdded` | `subject` (encja `Subject`), `classes` (co najmniej jedne zajęcia) | ponowne planowanie |
| `calendarExceptionAdded` | `exception` (wyjątek z `calendar.json`, np. dzień rektorski) | tylko terminarz |

`groupSizeChanged` zmienia tylko podaną grupę. Rozmiary przodków nie są przeliczane, bo model nie wymaga, żeby suma dzieci była równa rodzicowi.

## Generator

Zdarzenia przychodzą losowo (proces Poissona), średnio `events.per_week` na tydzień, od tygodnia przed semestrem do jego końca. Typy losowane są z wagami `events.weights`. Ustawienia są w sekcji `[events]` presetu (`src/krok/generator/settings.py`, `EventSpec`), a intensywność można nadpisać przez `--per-week`.

- Zdarzenia odwołują się do sal, prowadzących i grup z danych z #7. Nowe grupy i przedmioty dostają nowe, unikalne `id`.
- Część blokad i nieobecności jest długa (`long_share`, 2–8 tygodni), reszta krótka (1–5 dni). Część blokad sal dotyczy tylko kilku godzin.
- Nowa dostępność prowadzącego powstaje z tych samych wzorców co w generatorze, w tych samych trybach (dni robocze lub weekend).
- Nowa grupa laboratoryjna dostaje kopię zajęć grupy rodzeństwa. Nowy przedmiot obieralny ma wykład dla grup wykładowych rocznika i laboratorium dla jego grup laboratoryjnych.
- Ten sam seed daje ten sam strumień, a strumień ma własny strumień losowy, niezależny od danych.

## Celowo błędne zdarzenia

Część linii (`events.error_rate`, domyślnie 10%, `--no-errors` wyłącza) jest zepsuta. Każda trafia do `_events.json` (`errorsList`: `line`, `eventId`, `kind`, `field`, `before`, `after`), więc #9 i #11 mogą policzyć, ile wykryły.

| `kind` | Co jest zepsute | Jak to wykryć |
|---|---|---|
| `badReference` | odwołanie do nieistniejącej sali, prowadzącego, grupy itd. | porównanie z danymi |
| `missingField` | brak wymaganego pola w `payload` | schemat |
| `invalidValue` | `from` po `to`, liczebność ≤ 0, nowy przedmiot bez zajęć | schemat |
| `unknownType` | literówka w `type` | schemat |
| `duplicate` | to samo zdarzenie (to samo `id`) drugi raz | powtórzone `id` |
| `outOfOrder` | `timestamp` wcześniejszy niż w poprzednim zdarzeniu | porównanie z najpóźniejszym dotąd `timestamp` |
| `malformedJson` | ucięta linia | `json.loads` |

Poprawne zdarzenia odwołują się tylko do danych bazowych, więc zepsute zdarzenie nie unieważnia kolejnych.

## Odtwarzanie

```python
from krok.events import replay

for item in replay("data/generated/kierunek/events.jsonl", speed=86400):
  if item.event:          # poprawny schemat: item.event to model pydantic
    ...
  else:                   # item.error: ucięty JSON, brak pola, zła wartość, nieznany typ
    ...
```

- `speed`: sekundy symulacji na sekundę rzeczywistą. Bez `speed` iterator nie czeka. Przy cofniętym `timestamp` też nie czeka.
- `start` / `until` pomijają zdarzenia spoza zakresu czasu. Linie bez czytelnego czasu zawsze przechodzą.
- Iterator nie odrzuca błędnych linii, tylko je oznacza (`StreamItem.line`, `text`, `data`, `event`, `error`). Odwołań do danych, duplikatów i kolejności nie sprawdza, bo to zadanie percepcji (#9).
