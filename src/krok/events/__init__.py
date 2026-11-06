"""Strumień zdarzeń z otoczenia (#8): generator spójny z danymi z #7 i odtwarzanie jako iterator."""

from .generate import EventStreamResult, generate_events
from .stream import StreamItem, parse_line, replay

__all__ = ["EventStreamResult", "generate_events", "StreamItem", "parse_line", "replay"]
