# Dane bazowe Wydziału Informatyki (WJ)

- `config.json` — zasady układania (`timeGrid`, typy sal, `walkingSpeedKmh`, `routeFactor`, reguły). Reguły to pełna lista z `docs/etap1/kryteria.md` (#3); test pilnuje, że obie listy są takie same.
- `buildings.json` — budynki P1–P6. Współrzędne są przybliżone: wokół adresu ul. Prószkowska 76, układ budynków szacunkowy.
- `rooms.json` — sale **testowe** (#6). Układ jest prawdziwy, a typy i pojemności wymyślone.

## Układ sal

- Sala nazywa się `<budynek>-<numer>`, np. `P3-213`: budynek P3, 2. piętro, sala 13.
- Budynek ma 4 kondygnacje (0–3) i 15 sal na każdej, numery `x01`–`x15`. Razem 300 sal.
- P4 nie ma sal dydaktycznych, więc nie jest niczyim budynkiem macierzystym.
- P6 to centrum językowe, ma same sale `classroom`.

Generator (#7) czyta oba pliki stąd albo z `--buildings` / `--rooms`. W `rooms.json` można pominąć `capacity`, wtedy generator ją wylosuje. `tests/test_base_data.py` sprawdza ten układ, więc po wpisaniu prawdziwych danych wystarczy podmienić pliki i dopasować test, jeśli układ się zmieni.
