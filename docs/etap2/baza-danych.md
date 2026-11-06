# Baza danych, import i czyszczenie (#9)

Baza SQLite trzyma dane wejściowe po czyszczeniu i stan po zdarzeniach z otoczenia. Czyta je harness, a wkrótce percepcja (#10, #11).

```bash
uv run krok-gen --preset mini --noise --out data/generated/mini
uv run krok-db import data/generated/mini      # zastępuje zawartość bazy
uv run krok-db events data/generated/kierunek/events.jsonl
uv run krok-db report                          # liczby i przykłady
uv run krok-db report --json raport.json
```

Plik bazy to `data/krok.db`. Inny można wskazać przez `--db` albo zmienną `KROK_DB`.

## Schemat

Tabele powstają z modeli pydantic w `src/krok/model/` (`src/krok/db/schema.py`), więc nie ma drugiej wersji formatu. Kolumny nazywają się jak pola JSON. Wiersz to wynik `model_dump`, a `read_dataset(engine)` zwraca zwykły `krok.model.Dataset`.

| Tabela | Zawartość |
|---|---|
| `faculties`, `fields`, `programs`, `groups`, `buildings`, `rooms`, `lecturers`, `subjects`, `classes` | encje z kluczami obcymi; pola zagnieżdżone, np. `availability`, w kolumnach JSON |
| `documents` | `config` i `calendar` jako dokumenty JSON |
| `import_issues` | problemy z importu |
| `events` | dziennik zdarzeń: każda linia strumienia ze statusem |

## Czyszczenie

Zaszumione pliki nie przechodzą przez `load_dataset`, bo jeden zły rekord unieważniłby cały plik. Dlatego `import_dir` sprawdza każdy rekord osobno. Problem trafia do raportu, a import idzie dalej. Przerywa go tylko brak albo błąd `config.json` lub `calendar.json`, bo bez nich nie ma z czego zbudować bazy.

Kolejność kroków:

1. **Nazwy pól.** Literówki poprawiamy przez dopasowanie do pól modelu, których w rekordzie brakuje. Stare nazwy z prototypu też są mapowane (`Profesion`, `professions` → `competences`, `FavouriteBuilding` → `homeBuildingId`).
2. **Duplikaty.** Rozpoznajemy dokładną kopię, kopię pod innym `id` i ten sam `id` z inną treścią. Grupy rodzeństwa mogą mieć identyczną treść, więc kopia grupy musi mieć też `id` oryginału z numerem, np. `-2`.
3. **Walidacja modelem.**
4. **Wartości odstające.** Porównujemy z podobnymi rekordami: wartość zerowa albo ponad 4 × mediana (`Thresholds`) oraz budynek ponad 0,1° od pozostałych.
5. **Odwołania.** `reference_errors` działa na tym, co zostało. Rekord z wiszącym odwołaniem odpada, a kroki powtarzamy, bo usunięcie może osierocić kolejne rekordy.

Prowadzący spoza bazy wypada tylko z puli, a przedmiot zostaje. Inaczej przez jedną osobę stracilibyśmy przedmiot i wszystkie jego zajęcia.

| `action` | Znaczenie |
|---|---|
| `fixed` | rekord naprawiony albo usunięta zbędna kopia |
| `rejected` | rekordu nie ma w bazie |
| `flagged` | rekord jest w bazie, ale wartość jest wątpliwa |

| `kind` | Co oznacza |
|---|---|
| `keyTypo`, `unknownField` | literówka w nazwie pola, nieznane pole |
| `missingField`, `missingFile` | brak pola, brak pliku |
| `invalidValue`, `outlier` | zła wartość, wartość odstająca |
| `exactDuplicate`, `nearDuplicate`, `duplicateId` | duplikaty |
| `badReference` | odwołanie do czegoś, czego nigdy nie było |
| `cascade` | skutek odrzucenia innego rekordu |

Testy porównują wynik z `_noise.json`. Jedyne, czego nie da się wykryć, to literówka w odwołaniu, która trafia w inny istniejący `id`.

## Zdarzenia

`apply_events` czyta strumień przez `krok.events.replay` i sprawdza to, czego iterator nie sprawdza: odwołania, powtórzone `id` i kolejność.

| `type` | Skutek w bazie |
|---|---|
| `roomUnavailable`, `lecturerAbsent` | `resourceBlock` w kalendarzu, `id` bloku = `id` zdarzenia |
| `lecturerAvailabilityChanged` | nowa `availability` prowadzącego |
| `groupSizeChanged` | nowy `size` grupy |
| `groupAdded`, `subjectAdded` | nowe rekordy z zajęciami |
| `calendarExceptionAdded` | wyjątek w kalendarzu |

| `status` | Znaczenie |
|---|---|
| `applied` | zastosowane; `kind = outOfOrder`, gdy `timestamp` jest cofnięty, bo spóźniona informacja nadal jest prawdziwa |
| `rejected` | błąd schematu albo odwołania, stan się nie zmienia; powód w `kind` i `message` |
| `duplicate` | to samo `id` już zastosowane |

Ponowne odtworzenie tego samego pliku niczego nie zmienia w danych.

**Ograniczenie:** zdarzenie zmienia stan od razu, także gdy obowiązuje od późniejszej daty, bo model trzyma jeden stan bez historii. Data obowiązywania zostaje w dzienniku (`raw`). Ponowny `krok-db import` czyści też dziennik.

## Harness

`get_data` czyta z bazy. `src/krok/db/legacy.py` zwraca dane w kształcie starego prototypu, bo tych pól używają prompt agenta i `validate_plan`. Widok można usunąć, gdy powstaną #13 i #17. Bez bazy narzędzie zwraca komunikat z poleceniem `krok-db import`.

Raport z `krok-db report` to punkt wyjścia dla raportu jakości z #11.
