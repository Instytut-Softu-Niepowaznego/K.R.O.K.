# Dane bazowe Wydziału Informatyki (WJ)

- `config.json` — zasady układania (`timeGrid`, typy sal, `walkingSpeedKmh`, reguły). Wartości orientacyjne, docelowe reguły ustala #3.
- `buildings.json`, `rooms.json` — **tymczasowe**, do czasu #6: współrzędne P1–P6 są przybliżone, numery i pojemności sal wymyślone.
  Gdy #6 będzie gotowe, wystarczy podmienić te dwa pliki (format z `docs/etap1/model-domeny.md`). Generator (#7) czyta je stąd
  albo z `--buildings` / `--rooms`. W `rooms.json` można pominąć `capacity` — generator wtedy ją wylosuje.
