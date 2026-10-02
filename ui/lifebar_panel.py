"""Módulo da GUI: Sprite life bar (a barra de vida do pokémon na pokebar) + teste de leitura da % de vida.

Igual à 'Sprite da seta': o usuário desenha uma caixa, o recorte mágico isola a barra e ela é salva em lifebar.png.
Capture com a vida CHEIA e inclua a moldura da barra: a sprite só serve para achar a barra e saber o tamanho dela
cheia; a cor do preenchimento muda com a vida (verde, amarelo, vermelho) e isso não atrapalha. Sem a sprite, o
'Local Life Bar' precisa ter a mesma largura da barra (sobrar altura não atrapalha).
"""
from __future__ import annotations
import tkinter as tk
from tkinter import ttk

import cv2
import numpy as np
from PIL import Image

from core import capture
from core.config import ConfigStore, LIFEBAR_PATH
from core.lifebar import LifeBarReader
from core.magic_cut import load_template, save_template
from core.vision import SpriteTemplate
from .imgutil import over_checkerboard, to_photo
from .magic_cut_dialog import MagicCutDialog
from .region_selector import crop_region, select_region


class LifeBarPanel(ttk.LabelFrame):
    def __init__(self, master, cfg: ConfigStore):
        super().__init__(master, text=" Sprite life bar ")
        self.cfg = cfg
        self._photo = None
        self._preview_win = None

        self.preview = ttk.Label(self, text="(nenhuma barra)", anchor="center", relief="sunken", width=18)
        self.preview.grid(row=0, column=0, rowspan=4, padx=10, pady=8, ipady=18)

        ttk.Button(self, text="Selecionar barra de vida na tela", command=self.pick).grid(
            row=0, column=1, sticky="w", padx=6, pady=(10, 2))
        ttk.Label(self, text="Capture com a vida CHEIA (100%), com a moldura da barra.\n"
                             "Sem sprite: deixe o 'Local Life Bar' com a MESMA LARGURA da barra.",
                  foreground="#666", justify="left").grid(row=1, column=1, sticky="w", padx=6)

        srow = ttk.Frame(self)
        srow.grid(row=2, column=1, sticky="w", padx=6, pady=2)
        ttk.Label(srow, text="Similaridade mínima").pack(side="left")
        self.lim = tk.DoubleVar(value=float(cfg["vida_limiar"]))
        ttk.Spinbox(srow, from_=0.2, to=1.0, increment=0.01, format="%.2f", width=5,
                    textvariable=self.lim).pack(side="left", padx=6)
        self.lim.trace_add("write", lambda *a: self._save_lim())

        ttk.Button(self, text="Testar leitura da vida", command=self.test_read).grid(
            row=3, column=1, sticky="w", padx=6, pady=(2, 10))
        self.test_lbl = ttk.Label(self, text="", wraplength=420, justify="left")
        self.test_lbl.grid(row=4, column=0, columnspan=2, sticky="w", padx=10, pady=(0, 8))
        self.refresh()

    # ---------------------------------------------------------------- ações
    def _save_lim(self) -> None:
        try:
            self.cfg.set("vida_limiar", round(float(self.lim.get()), 2))
        except (tk.TclError, ValueError):
            pass  # campo vazio durante a digitação

    def pick(self) -> None:
        region, shot = select_region(
            self, "Desenhe uma caixa que CONTENHA a barra de vida cheia (com a moldura)  •  Esc cancela")
        if not region:
            return
        dlg = MagicCutDialog(self, crop_region(shot, region))
        self.wait_window(dlg)
        if dlg.result is not None:
            save_template(dlg.result, LIFEBAR_PATH)
            self.refresh()
            self.test_lbl.config(text="")

    def refresh(self) -> None:
        loaded = load_template(LIFEBAR_PATH)
        if loaded is None:
            return
        bgr, mask = loaded
        rgba = cv2.cvtColor(np.dstack([bgr, mask]), cv2.COLOR_BGRA2RGBA)
        self._photo = to_photo(over_checkerboard(rgba), 150, 60)
        self.preview.config(image=self._photo, text="", width=0)

    def test_read(self) -> None:
        region = self.cfg.get("regiao_vida")
        if not region:
            self.test_lbl.config(text="Defina o 'Local Life Bar' abaixo primeiro.")
            return
        tmpl = SpriteTemplate.from_file(LIFEBAR_PATH)
        frame = capture.grab(region)
        if tmpl is not None and (frame.shape[0] < tmpl.h or frame.shape[1] < tmpl.w):
            self.test_lbl.config(text="A sprite da barra é maior que o 'Local Life Bar'.")
            return
        r = LifeBarReader(tmpl, float(self.cfg["vida_limiar"])).read(frame)
        if r.pct is None:
            self.test_lbl.config(text=f"Barra NÃO encontrada no 'Local Life Bar' (melhor similaridade {r.score:.2f}, "
                                      f"mínimo {self.cfg['vida_limiar']:.2f}). Baixe a similaridade mínima, ou capture a "
                                      "sprite de novo com a moldura da barra.")
            return
        how = f"similaridade {r.score:.2f}" if tmpl is not None else "sem sprite: área inteira"
        self.test_lbl.config(text=f"Vida lida: {r.pct:.0f}%  ({r.cols} de {r.full_cols} colunas cheias; {how}).")
        self._show_preview(frame, r)

    def _show_preview(self, frame, r) -> None:
        img = frame.copy()
        x, y, w, h = r.box
        cv2.rectangle(img, (x - 1, y - 1), (x + w, y + h), (255, 0, 255), 1)
        if self._preview_win is not None and self._preview_win.winfo_exists():
            self._preview_win.destroy()
        win = tk.Toplevel(self)
        win.title(f"Vida lida: {r.pct:.0f}%  (retângulo magenta = onde o bot achou a barra)")
        self._preview_win = win
        photo = to_photo(Image.fromarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB)), 700, 300, max_scale=6)
        lbl = ttk.Label(win, image=photo)
        lbl.image = photo  # evita o garbage collector
        lbl.pack(padx=8, pady=8)
