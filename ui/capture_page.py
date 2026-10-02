"""Página 'sprites/capture': sprite de parada, sprite da seta, áreas de captura (busca da sprite e mapa onde a seta está) e mapa completo."""
from __future__ import annotations
from tkinter import ttk

from core.config import ConfigStore
from .areas_panel import AreasPanel
from .arrow_panel import ArrowPanel
from .map_panel import FullMapPanel
from .sprite_panel import SpritePanel


class CapturePage(ttk.Frame):
    def __init__(self, master, cfg: ConfigStore):
        super().__init__(master)
        self.sprite_panel = SpritePanel(self, cfg)
        self.areas_panel = AreasPanel(self, cfg)
        self.arrow_panel = ArrowPanel(self, cfg)
        self.full_map_panel = FullMapPanel(self, cfg)
        self.sprite_panel.grid(row=0, column=0, sticky="nsew", padx=(10, 5), pady=6)
        self.arrow_panel.grid(row=0, column=1, sticky="nsew", padx=(5, 10), pady=6)
        self.areas_panel.grid(row=1, column=0, columnspan=2, sticky="ew", padx=10, pady=6)
        self.full_map_panel.grid(row=2, column=0, columnspan=2, sticky="ew", padx=10, pady=6)
