"""Página 'shooter': sprite do seu pokémon (o nome muda de cor com a vida) e o tiro na sprite distante que parou."""
from __future__ import annotations
from tkinter import ttk

from core.config import ConfigStore
from .pokemon_panel import PokemonPanel
from .shooter_panel import ShooterPanel


class ShooterPage(ttk.Frame):
    def __init__(self, master, cfg: ConfigStore):
        super().__init__(master)
        self.pokemon_panel = PokemonPanel(self, cfg)
        self.pokemon_panel.grid(row=0, column=0, sticky="nsew", padx=10, pady=8)
        self.shooter_panel = ShooterPanel(self, cfg)
        self.shooter_panel.grid(row=1, column=0, sticky="nsew", padx=10, pady=(0, 8))
