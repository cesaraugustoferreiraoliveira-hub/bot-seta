"""Módulo da GUI: Sprite de parada (escolher a sprite com recorte mágico + quantidade)."""
from __future__ import annotations
import tkinter as tk
from tkinter import ttk

import cv2
import numpy as np

from core import capture, vision
from core.config import ConfigStore, SPRITE_PATH
from core.magic_cut import load_template, save_template
from .imgutil import over_checkerboard, to_photo
from .magic_cut_dialog import MagicCutDialog
from .region_selector import crop_region, select_region


class SpritePanel(ttk.LabelFrame):
    def __init__(self, master, cfg: ConfigStore):
        super().__init__(master, text=" Sprite de parada ")
        self.cfg = cfg
        self._photo = None

        self.preview = ttk.Label(self, text="(nenhuma sprite)", anchor="center", relief="sunken", width=16)
        self.preview.grid(row=0, column=0, rowspan=3, padx=10, pady=8, ipady=18)

        ttk.Button(self, text="Selecionar sprite na tela", command=self.pick_sprite).grid(
            row=0, column=1, sticky="w", padx=6, pady=(10, 2))

        qrow = ttk.Frame(self)
        qrow.grid(row=1, column=1, sticky="w", padx=6, pady=2)
        ttk.Label(qrow, text="Parar a seta com ≥").pack(side="left")
        self.qtd = tk.IntVar(value=int(cfg["sprite_qtd"]))
        ttk.Spinbox(qrow, from_=1, to=99, width=4, textvariable=self.qtd).pack(side="left", padx=6)
        self.qtd.trace_add("write", lambda *a: self._save_qtd())
        ttk.Label(qrow, text="sprites").pack(side="left")

        trow = ttk.Frame(self)
        trow.grid(row=2, column=1, sticky="w", padx=6, pady=(2, 10))
        ttk.Button(trow, text="Testar detecção", command=self.test_detect).pack(side="left")
        self.test_lbl = ttk.Label(trow, text="")
        self.test_lbl.pack(side="left", padx=8)

        self.refresh()

    # ---------------------------------------------------------------- ações
    def _save_qtd(self) -> None:
        try:
            self.cfg.set("sprite_qtd", int(self.qtd.get()))
        except (tk.TclError, ValueError):
            pass  # campo vazio durante a digitação

    def pick_sprite(self) -> None:
        region, shot = select_region(
            self, "Desenhe uma caixa que CONTENHA a sprite (com um pouco de fundo em volta)  •  Esc cancela")
        if not region:
            return
        dlg = MagicCutDialog(self, crop_region(shot, region))
        self.wait_window(dlg)
        if dlg.result is not None:
            save_template(dlg.result, SPRITE_PATH)
            self.refresh()
            self.test_lbl.config(text="")

    def refresh(self) -> None:
        loaded = load_template(SPRITE_PATH)
        if loaded is None:
            return
        bgr, mask = loaded
        rgba = cv2.cvtColor(np.dstack([bgr, mask]), cv2.COLOR_BGRA2RGBA)
        self._photo = to_photo(over_checkerboard(rgba), 110, 90)
        self.preview.config(image=self._photo, text="", width=0)

    def test_detect(self) -> None:
        if not self.cfg.get("regiao_sprite"):
            self.test_lbl.config(text="Defina a área de busca da sprite.")
            return
        tmpl = vision.SpriteTemplate.from_file(SPRITE_PATH)
        if tmpl is None:
            self.test_lbl.config(text="Selecione a sprite primeiro.")
            return
        frame = capture.grab(self.cfg["regiao_sprite"])
        if frame.shape[0] < tmpl.h or frame.shape[1] < tmpl.w:
            self.test_lbl.config(text="A sprite é maior que a área de busca.")
            return
        n = vision.count_sprites(frame, tmpl, self.cfg["sprite_limiar"])
        best = float(vision.sprite_scores(frame, tmpl).max())
        parar = "  → a seta PARARIA" if n >= self.cfg["sprite_qtd"] else ""
        self.test_lbl.config(text=f"Detectadas: {n} (melhor similaridade {best:.2f}){parar}")
