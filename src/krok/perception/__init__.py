"""Percepcja (#10): stan otoczenia i raport wykonalności z danych wejściowych."""

from .feasibility import FeasibilityReport, Problem, analyze
from .measures import distance_km, meeting_minutes, travel_matrix, travel_minutes
from .state import ClassInfo, EnvironmentState, build_state

__all__ = ["FeasibilityReport", "Problem", "analyze", "distance_km", "meeting_minutes", "travel_matrix", "travel_minutes",
           "ClassInfo", "EnvironmentState", "build_state"]
