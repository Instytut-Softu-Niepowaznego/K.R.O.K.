"""Losowe imiona i nazwiska prowadzących."""

import random

MALE = ["Adam", "Andrzej", "Bartosz", "Damian", "Grzegorz", "Jakub", "Jan", "Jerzy", "Kamil", "Krzysztof", "Łukasz",
        "Marcin", "Marek", "Michał", "Paweł", "Piotr", "Robert", "Tomasz", "Wojciech", "Zbigniew"]
FEMALE = ["Agnieszka", "Aleksandra", "Anna", "Barbara", "Ewa", "Joanna", "Justyna", "Katarzyna", "Magdalena", "Małgorzata",
          "Maria", "Marta", "Monika", "Natalia", "Paulina", "Renata", "Agata", "Beata", "Dorota", "Izabela"]
# (forma męska, forma żeńska)
SURNAMES = [("Nowak", "Nowak"), ("Kowalski", "Kowalska"), ("Wiśniewski", "Wiśniewska"), ("Wójcik", "Wójcik"),
            ("Kowalczyk", "Kowalczyk"), ("Kamiński", "Kamińska"), ("Lewandowski", "Lewandowska"), ("Zieliński", "Zielińska"),
            ("Szymański", "Szymańska"), ("Woźniak", "Woźniak"), ("Dąbrowski", "Dąbrowska"), ("Kozłowski", "Kozłowska"),
            ("Jankowski", "Jankowska"), ("Mazur", "Mazur"), ("Kwiatkowski", "Kwiatkowska"), ("Krawczyk", "Krawczyk"),
            ("Piotrowski", "Piotrowska"), ("Grabowski", "Grabowska"), ("Nowakowski", "Nowakowska"), ("Pawłowski", "Pawłowska"),
            ("Michalski", "Michalska"), ("Nowicki", "Nowicka"), ("Adamczyk", "Adamczyk"), ("Dudek", "Dudek"),
            ("Zając", "Zając"), ("Wieczorek", "Wieczorek"), ("Jabłoński", "Jabłońska"), ("Król", "Król"),
            ("Majewski", "Majewska"), ("Olszewski", "Olszewska"), ("Jaworski", "Jaworska"), ("Wróbel", "Wróbel"),
            ("Malinowski", "Malinowska"), ("Pawlak", "Pawlak"), ("Witkowski", "Witkowska"), ("Walczak", "Walczak")]
TITLES = [("prof. dr hab. inż.", 1), ("dr hab. inż.", 2), ("dr inż.", 6), ("dr", 3), ("mgr inż.", 4), ("mgr", 2)]


class NameGenerator:
  def __init__(self, rng: random.Random):
    self.rng = rng
    self.used: set[str] = set()

  def __call__(self) -> str:
    titles, weights = zip(*TITLES)
    for _ in range(1000):
      female = self.rng.random() < 0.45
      first = self.rng.choice(FEMALE if female else MALE)
      surname = self.rng.choice(SURNAMES)[1 if female else 0]
      name = f"{self.rng.choices(titles, weights)[0]} {first} {surname}"
      if name not in self.used:
        self.used.add(name)
        return name
    raise RuntimeError("Zabrakło unikalnych imion i nazwisk")
