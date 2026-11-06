# Generator danych wydziału (#7)

Generator buduje realistyczny wydział, wzorowany na Wydziale Informatyki (WJ) Politechniki Opolskiej, w formacie z `docs/etap1/model-domeny.md`. Skalę reguluje preset: od kilku grup do całego wydziału.

```bash
uv run krok-gen --list                                  # dostępne presety
uv run krok-gen --preset wydzial --seed 42 --out data/generated/wydzial
uv run krok-gen --preset kierunek --noise               # dane z szumem do testów percepcji
uv run krok-gen --preset mini --rooms moje/rooms.json   # inne sale
```

Ten sam preset i ten sam `--seed` dają identyczne pliki. Każdy etap (struktura, program, prowadzący, kalendarz, szum) ma własny strumień losowy, więc włączenie szumu nie zmienia czystych danych.

## Wyjście

Pliki z tabeli „Wejścia” w modelu domeny: `config.json`, `calendar.json`, `faculties.json`, `groups.json`, `buildings.json`, `rooms.json`, `lecturers.json`, `subjects.json`, `classes.json`. Dochodzą do nich:

- `_meta.json`: preset, seed, liczności i lista wstrzykniętych przypadków brzegowych z `id` encji, których dotyczą;
- `_noise.json` (tylko z `--noise`): każda zmiana wprowadzona przez szum (`file`, `id`, `kind`, `field`, `before`, `after`).

Format plików definiują modele pydantic w `src/krok/model/`, które są jedynym źródłem prawdy. Generator buduje obiekty tych modeli i zapisuje je przez `model_dump`, więc zmiana modelu od razu zmienia wyjście. `tests/test_model.py` sprawdza, że przykłady JSON z `model-domeny.md` przechodzą walidację modeli.

## Presety

| Preset | Zawartość |
|---|---|
| `mini` | Informatyka inż. dzienne, tylko 3. semestr: 1 grupa wykładowa, 2 ćwiczeniowe, 4 laboratoryjne |
| `kierunek` | Informatyka: inż. i mgr, dzienne i zaoczne, wszystkie roczniki semestru zimowego |
| `wydzial` | Informatyka, Automatyka i robotyka, Elektronika i telekomunikacja (lista orientacyjna) |
| `edge-cases` | Informatyka dzienne i zaoczne (3. semestr) z przypadkami brzegowymi |

Presety to pliki TOML w `src/krok/generator/presets/`. Można podać własny plik: `--preset moj.toml`. Wpis `extends = "mini"` dziedziczy preset i nadpisuje wybrane pola. Pełna lista pól jest w `src/krok/generator/settings.py` (`Preset`).

Przedmioty i kompetencje pochodzą z katalogu `presets/katalog-wj.toml`. Dla każdego przedmiotu katalog podaje stopień, zakres semestrów, kierunki, jednostki na formę (W/C/L/P) i typ sali laboratoryjnej. Kompetencja z `external = true` oznacza prowadzących spoza wydziału (matematyka, fizyka, języki), którzy mają mniej dni dostępności.

## Jak powstają dane

1. **Grupy:** rocznik na 1. semestrze ma rozmiar `cohort_size` i maleje o `attrition_per_year` z każdym rokiem. Dzieli się na grupy wykładowe (≤ `lecture_max`), ćwiczeniowe (≤ `exercise`) i laboratoryjne (≤ `lab`). Domyślnie aktywne są semestry nieparzyste (semestr zimowy).
2. **Zajęcia:** dla każdego rocznika losowane są przedmioty pasujące do kierunku, stopnia i semestru, każdy najwyżej raz w programie. Formy przypisane są do grup: W → wykładowe, C → ćwiczeniowe, L i P → laboratoryjne. Studia zaoczne mają `part_time_units_factor` jednostek. Część laboratoriów (`ab_share`) odbywa się co dwa tygodnie, grupy rodzeństwa na zmianę A i B.
3. **Prowadzący:** liczba wynika z zapotrzebowania (jednostki × `slack` / średni limit), osobno dla studiów dziennych i zaocznych. Część zaocznych (`cross_mode_share`) prowadzi kadra dzienna z dodaną dostępnością w weekend. Dostępność jest w godzinach i powstaje według wzorców (`pelny`, `rano`, `popoludnie`, `czesciowy`). Prowadzący dostają też losowe dodatkowe kompetencje i budynek macierzysty zgodny z kompetencją.
4. **Obsada (`lecturerPools`):** część przedmiotów (`lecturers.pool_share`, domyślnie 60%) dostaje pule prowadzących per forma. Pula zawiera tylko osoby z kompetencją przedmiotu, ma co najmniej jedną osobę dostępną w dni robocze i jedną w weekend, jeśli forma ma zajęcia dzienne i zaoczne, a jej łączny limit pokrywa zapotrzebowanie formy × `slack`. Gdy tego nie da się spełnić, forma zostaje bez puli i solver wybiera spośród wszystkich prowadzących z kompetencją.
5. **Kalendarz:** semestr, polskie święta (z Wielkanocą i Bożym Ciałem dla semestru letniego), przerwy, dni i godziny rektorskie, odpracowanie (`dayOverride`), blokady sal, zjazdy zaoczne co `part_time_every_weeks` tygodni oraz tygodnie bez parzystości (cały tydzień wolny).
6. **Sale i budynki:** wczytywane z `src/krok/data/wj/` (#6). To dane **testowe**: układ sal jest prawdziwy, a typy i pojemności wymyślone. Brakująca `capacity` jest losowana z `room_capacity`.

Na końcu całość przechodzi walidację modelu i sprawdzenie referencji (`krok.model.reference_errors`). Podsumowanie w CLI pokazuje zgrubny bilans sal i prowadzących. Dokładny bilans liczy #10.

## Przypadki brzegowe (`edge-cases`)

| `case` | Co jest w danych |
|---|---|
| `grupa-wieksza-od-sal` | grupa laboratoryjna większa od każdej sali swojego typu |
| `przedmiot-bez-prowadzacego` | przedmiot z kompetencją, której nie ma żaden prowadzący |
| `prowadzacy-bez-dostepnosci` | prowadzący z pustą dostępnością i przypisanymi zajęciami |
| `limit-ponizej-obciazenia` | prowadzący z limitem 2 jednostek i przypisanymi zajęciami na więcej |
| `zajecia-1-jednostkowe` | zajęcia trwające 45 min |
| `zajecia-ab` | laboratoria grup rodzeństwa w tygodniach A i B |
| `prowadzacy-dzienne-i-zaoczne` | prowadzący przypisany do zajęć dziennych i zaocznych |
| `zajecia-dluzsze-niz-dostepnosc` | zajęcia 2-jednostkowe u prowadzącego z 60 min ciągłej dostępności |
| `pula-niedostepna` | pula formy złożona z jednej osoby dostępnej tylko w sobotę, przy zajęciach dziennych |
| `pula-za-maly-limit` | pula formy z łącznym limitem 2 jednostek, mniejszym niż zapotrzebowanie |
| `nakladajace-sie-wyjatki` | dzień rektorski w przerwie świątecznej, blokada sali obejmująca święto |
| `odpracowanie-w-dniu-wolnym` | `dayOverride` w dniu ustawowo wolnym |

Przypadki można też włączyć dla dowolnego presetu: `--edge-cases`.

## Szum (`--noise`)

Szum jest nakładany na gotowe pliki, więc dane z szumem celowo nie przechodzą walidacji modelu. Służy do testowania importu (#9) i raportu jakości (#11). Rodzaje zmian i ich częstość ustawia się w presecie (`[noise]`):

- `missingField`: usunięte pole;
- `keyTypo`: literówka w nazwie pola (np. `kind` → `kid`);
- `badReference`: odwołanie do nieistniejącego `id`;
- `outlier`: wartość odstająca (0, ujemna, ×10, współrzędne przesunięte o 5°);
- `exactDuplicate` / `nearDuplicate`: ten sam rekord albo jego kopia z innym `id`.

Każda zmiana trafia do `_noise.json`, więc #11 może policzyć, ile z nich wykryła percepcja.

Zdarzenia w trakcie semestru do tych samych danych generuje `krok-events` (#8), opis w [zdarzenia.md](zdarzenia.md).
