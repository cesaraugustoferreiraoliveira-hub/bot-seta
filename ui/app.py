"""Janela principal: só importa os módulos da GUI e os junta."""
from __future__ import annotations
import ctypes
import sys
import tkinter as tk
from tkinter import ttk

from core.config import ConfigStore
from core.botlog import BotLog
from .capture_page import CapturePage
from .engine_page import EnginePage
from .shooter_page import ShooterPage
from .revive_page import RevivePage
from .pokeball_page import PokeballPage


def _enable_dpi_awareness() -> None:
    """Evita desencontro de coordenadas entre o tkinter e a captura em telas com escala (Windows)."""
    if sys.platform == "win32":
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:  # noqa: BLE001
            try:
                ctypes.windll.user32.SetProcessDPIAware()
            except Exception:  # noqa: BLE001
                pass


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Bot da seta")
        self.resizable(False, False)
        self.cfg = ConfigStore()
        self.log = BotLog()
        # Para adicionar uma página nova: crie a classe em ui/ e inclua uma linha aqui.
        tabs = ttk.Notebook(self)
        tabs.pack(fill="both", expand=True)
        self.capture_page = CapturePage(tabs, self.cfg)
        self.engine_page = EnginePage(tabs, self.cfg, self.log)
        self.shooter_page = ShooterPage(tabs, self.cfg)
        self.revive_page = RevivePage(tabs, self.cfg)
        self.pokeball_page = PokeballPage(tabs, self.cfg, self.log)
        tabs.add(self.capture_page, text="sprites/capture")
        tabs.add(self.engine_page, text="engine")
        tabs.add(self.shooter_page, text="shooter")
        tabs.add(self.revive_page, text="revive")
        tabs.add(self.pokeball_page, text="pokeball")
        tabs.bind("<<NotebookTabChanged>>", lambda e: self.capture_page.full_map_panel.refresh())
        self.protocol("WM_DELETE_WINDOW", self._close)

    def _close(self) -> None:
        self.engine_page.shutdown()  # solta as teclas W/A/S/D e remove o atalho global
        self.destroy()


def run() -> None:
    _enable_dpi_awareness()
    App().mainloop()
