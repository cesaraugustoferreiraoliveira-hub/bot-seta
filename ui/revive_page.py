"""Página 'revive': leitura da vida do pokémon (sprite life bar + Local Life Bar), foto de confirmação do revive,
comando Revive (toque rápido confirmado pela foto) e habilidades por % de vida."""
from __future__ import annotations
from tkinter import ttk

from core.config import ConfigStore
from .areas_panel import AreasPanel
from .key_capture import KeyCapture
from .life_skills_panel import LifeSkillsPanel
from .lifebar_panel import LifeBarPanel
from .revive_panel import RevivePanel

REVIVE_AREAS = [
    ("regiao_vida", "Local Life Bar", "Onde está a barra de vida do pokémon na pokebar (a % de vida é lida aqui)"),
    ("regiao_revive_foto", "Local da foto do revive", "Área tirada como 'foto': quando ela muda, o revive foi executado"),
]


class RevivePage(ttk.Frame):
    def __init__(self, master, cfg: ConfigStore):
        super().__init__(master)
        keys = KeyCapture(self)
        tabs = ttk.Notebook(self)
        tabs.pack(fill="both", expand=True, padx=4, pady=4)

        cap = ttk.Frame(tabs)
        self.lifebar_panel = LifeBarPanel(cap, cfg)
        self.areas_panel = AreasPanel(cap, cfg, REVIVE_AREAS, " Áreas de captura na tela ")
        self.lifebar_panel.grid(row=0, column=0, sticky="nsew", padx=10, pady=6)
        self.areas_panel.grid(row=1, column=0, sticky="ew", padx=10, pady=6)

        rev = ttk.Frame(tabs)
        self.revive_panel = RevivePanel(rev, cfg, keys)
        self.revive_panel.pack(fill="both", expand=True, padx=10, pady=6)

        hab = ttk.Frame(tabs)
        self.skills_panel = LifeSkillsPanel(hab, cfg, keys)
        self.skills_panel.pack(fill="both", expand=True, padx=10, pady=6)

        tabs.add(cap, text="Captura")
        tabs.add(rev, text="Revive")
        tabs.add(hab, text="Habilidades por vida")
