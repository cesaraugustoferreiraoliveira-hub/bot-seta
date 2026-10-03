"""Módulo da GUI: Áreas de captura na tela (busca da sprite e mapa onde a seta está)."""
from __future__ import annotations
from tkinter import ttk

from core import capture
from core.config import ConfigStore
from .imgutil import to_photo
from .region_selector import select_region

AREAS = [
    ("regiao_sprite", "Área de busca da sprite", "Onde o bot procura as sprites para parar a seta"),
    ("regiao_mapa", "Mapa onde a seta está", "Só para ler a posição da seta (coordenadas) a cada ciclo"),
]


class AreaRow:
    """Uma linha do painel: título, dica, coordenadas, botão e prévia ao vivo."""

    def __init__(self, parent, row: int, cfg: ConfigStore, key: str, titulo: str, dica: str):
        self.cfg, self.key = cfg, key
        self._photo = None
        ttk.Label(parent, text=titulo, font=("Segoe UI", 9, "bold")).grid(
            row=row * 2, column=0, sticky="w", padx=10, pady=(8, 0))
        ttk.Label(parent, text=dica, foreground="#666").grid(row=row * 2 + 1, column=0, sticky="nw", padx=10)
        self.coord = ttk.Label(parent, text="", width=26)
        self.coord.grid(row=row * 2, column=1, sticky="w", padx=6, pady=(8, 0))
        ttk.Button(parent, text="Selecionar área", command=self.pick).grid(
            row=row * 2 + 1, column=1, sticky="w", padx=6, pady=(0, 4))
        self.prev = ttk.Label(parent, text="sem prévia", anchor="center", relief="sunken", width=24)
        self.prev.grid(row=row * 2, column=2, rowspan=2, padx=10, pady=6, ipady=12)
        self.parent = parent
        self.refresh()

    def pick(self) -> None:
        region, _ = select_region(self.parent)
        if region:
            self.cfg.set(self.key, region)
            self.refresh()

    def refresh(self) -> None:
        r = self.cfg.get(self.key)
        if not r:
            self.coord.config(text="não definida")
            self.prev.config(image="", text="sem prévia", width=24)
            self._photo = None
            return
        self.coord.config(text=f"x={r[0]}  y={r[1]}  {r[2]}×{r[3]}")
        try:
            self._photo = to_photo(capture.grab_pil(r), 190, 110, max_scale=4)
            self.prev.config(image=self._photo, text="", width=0)
        except Exception:  # noqa: BLE001
            self.prev.config(image="", text="erro na prévia")


class AreasPanel(ttk.LabelFrame):
    def __init__(self, master, cfg: ConfigStore, areas=AREAS, title: str = " Áreas de captura na tela "):
        super().__init__(master, text=title)
        self.rows = [AreaRow(self, i, cfg, *spec) for i, spec in enumerate(areas)]
        ttk.Button(self, text="Atualizar prévias", command=self.refresh_all).grid(
            row=len(areas) * 2, column=2, sticky="e", padx=10, pady=(0, 10))

    def refresh_all(self) -> None:
        for row in self.rows:
            row.refresh()
