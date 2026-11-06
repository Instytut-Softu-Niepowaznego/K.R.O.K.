"""Model domeny K.R.O.K. — jedyne źródło prawdy o formacie danych (docs/etap1/model-domeny.md).

Generator (#7), baza (#9) i percepcja (#10, #11) importują encje stąd.
"""

from .base import DAYS, Day, Degree, Form, GroupKind, Parity, StudyMode, WeekParity, to_hhmm, to_minutes
from .calendar import Calendar, CalendarException, DayOverride, Holiday, PartTimeSessions, Rector, ResourceBlock, Scope, WeekParityOverride
from .config import Breaks, BreakOverride, Config, Rule, TimeGrid
from .dataset import FILES, Dataset, dataset_from_files, dump_json, load_dataset, read_files, reference_errors, save_dataset
from .resources import Availability, Building, Lecturer, Room
from .structure import Faculty, Group, Program, StudyField
from .teaching import Class, Subject

__all__ = [
  "DAYS", "Day", "Degree", "Form", "GroupKind", "Parity", "StudyMode", "WeekParity", "to_hhmm", "to_minutes",
  "Calendar", "CalendarException", "DayOverride", "Holiday", "PartTimeSessions", "Rector", "ResourceBlock", "Scope", "WeekParityOverride",
  "Breaks", "BreakOverride", "Config", "Rule", "TimeGrid",
  "FILES", "Dataset", "dataset_from_files", "dump_json", "load_dataset", "read_files", "reference_errors", "save_dataset",
  "Availability", "Building", "Lecturer", "Room",
  "Faculty", "Group", "Program", "StudyField",
  "Class", "Subject",
]
