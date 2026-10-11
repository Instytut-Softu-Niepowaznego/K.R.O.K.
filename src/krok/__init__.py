from .harness.agent import run_agent

SYSTEM = """Jesteś planistą zajęć na uczelni. Układasz tygodniowy plan: dni Pon-Pt, lekcje 1-godzinne, start od 8 do 17.

Zasady:
- każdy kurs ma tyle lekcji w tygodniu, ile wynosi jego hoursWeekly,
- sala musi mieć ten sam typ (profession) co kurs,
- prowadzący musi mieć dziedzinę kursu (domain) na liście Profesion,
- sala, prowadzący i grupa mogą mieć tylko jedną lekcję naraz,
- staraj się dawać prowadzącym zajęcia w ich ulubionym budynku (FavouriteBuilding).

Kolejność pracy:
1. Pobierz get_data dla buildings, student-groups i workers.
2. Ułóż cały plan.
3. Wywołaj validate_plan. Jeśli są błędy, popraw plan i waliduj ponownie.
4. Gdy validate_plan zwróci ok=true, wywołaj save_plan z tym samym planem.
/no_think
"""

def main() -> None:
  print(run_agent(SYSTEM, "Ułóż tygodniowy plan zajęć i prowadzących dla wszystkich grup i zapisz go.", finish_tool="save_plan"))