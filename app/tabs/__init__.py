"""Rejestr zakładek. Kolejność = kolejność w UI."""
from app.tabs import import_tab, overview_tab

REGISTRY = [
    {"name": "Import", "render": import_tab.render},
    {"name": "Przegląd", "render": overview_tab.render},
]
