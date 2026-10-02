"""Módulo da GUI: Sprite do seu pokémon (o nome sobre a cabeça dele) + teste de posição na área de busca.

O nome muda de cor com a vida (verde, amarelo, vermelho): a busca compara o FORMATO das letras e ignora a cor
(veja core/name_match.py), então a sprite pode ser capturada com o nome em qualquer cor.
"""
from __future__ import annotations
import tkinter as tk
from tkinter import ttk

import cv2
import numpy as np
from PIL import Image

from core import capture
from core.config import ConfigStore, POKEMON_PATH
from core.magic_cut import load_template, save_template
from core.name_match import NameTemplate, find_name, health_color_name
from .imgutil import over_checkerboard, to_photo
from .magic_cut_dialog import MagicCutDialog
from .region_selector import crop_region, select_region

VIDA = {"verde": "vida alta", "amarela": "vida pela metade", "vermelha": "vida baixa"}


class PokemonPanel(ttk.LabelFrame):
    def __init__(self, master, cfg: ConfigStore):
        super().__init__(master, text=" Sprite do seu pokémon ")
        self.cfg = cfg
        self._photo = None
        self._preview_win = None

        self.preview = ttk.Label(self, text="(nenhuma sprite)", anchor="center", relief="sunken", width=16)
        self.preview.grid(row=0, column=0, rowspan=4, padx=10, pady=8, ipady=18)

        ttk.Button(self, text="Selecionar nome do pokémon na tela", command=self.pick).grid(
            row=0, column=1, sticky="w", padx=6, pady=(10, 2))
        ttk.Label(self, text="Pode capturar com o nome em qualquer cor (verde, amarelo ou vermelho).",
                  foreground="#666").grid(row=1, column=1, sticky="w", padx=6)

        lrow = ttk.Frame(self)
        lrow.grid(row=2, column=1, sticky="w", padx=6, pady=2)
        ttk.Label(lrow, text="Similaridade mínima").pack(side="left")
        self.lim = tk.DoubleVar(value=float(cfg["pokemon_limiar"]))
        ttk.Spinbox(lrow, from_=0.3, to=1.0, increment=0.01, format="%.2f", width=5,
                    textvariable=self.lim).pack(side="left", padx=6)
        self.lim.trace_add("write", lambda *a: self._save_lim())

        ttk.Button(self, text="Testar posição na área de busca", command=self.test_detect).grid(
            row=3, column=1, sticky="w", padx=6, pady=(2, 10))
        self.test_lbl = ttk.Label(self, text="", wraplength=420, justify="left")
        self.test_lbl.grid(row=4, column=0, columnspan=2, sticky="w", padx=10, pady=(0, 8))
        self.refresh()

    # ---------------------------------------------------------------- ações
    def _save_lim(self) -> None:
        try:
            self.cfg.set("pokemon_limiar", round(float(self.lim.get()), 2))
        except (tk.TclError, ValueError):
            pass  # campo vazio durante a digitação

    def pick(self) -> None:
        region, shot = select_region(
            self, "Desenhe uma caixa que CONTENHA o nome do pokémon (com um pouco de fundo em volta)  •  Esc cancela")
        if not region:
            return
        dlg = MagicCutDialog(self, crop_region(shot, region))
        self.wait_window(dlg)
        if dlg.result is None:
            return
        try:
            NameTemplate(dlg.result[..., :3], dlg.result[..., 3])   # confere se a sprite serve antes de salvar
        except ValueError as exc:
            self.test_lbl.config(text=str(exc))
            return
        save_template(dlg.result, POKEMON_PATH)
        self.refresh()
        self.test_lbl.config(text="")

    def refresh(self) -> None:
        loaded = load_template(POKEMON_PATH)
        if loaded is None:
            return
        bgr, mask = loaded
        rgba = cv2.cvtColor(np.dstack([bgr, mask]), cv2.COLOR_BGRA2RGBA)
        self._photo = to_photo(over_checkerboard(rgba), 150, 60)
        self.preview.config(image=self._photo, text="", width=0)

    def test_detect(self) -> None:
        region = self.cfg.get("regiao_sprite")
        if not region:
            self.test_lbl.config(text="Defina a 'Área de busca da sprite' na aba sprites/capture primeiro.")
            return
        try:
            tmpl = NameTemplate.from_file(POKEMON_PATH)
        except ValueError as exc:
            self.test_lbl.config(text=str(exc))
            return
        if tmpl is None:
            self.test_lbl.config(text="Selecione o nome do pokémon primeiro.")
            return
        frame = capture.grab(region)
        m = find_name(frame, tmpl, self.cfg["pokemon_limiar"])
        if m is None:
            self.test_lbl.config(text="A sprite é maior que a área de busca.")
            return
        lim = self.cfg["pokemon_limiar"]
        if not m.found:
            self.test_lbl.config(text=f"Nome NÃO encontrado na área de busca (melhor similaridade {m.score:.2f}, "
                                      f"mínimo {lim:.2f}). Confira se o pokémon está na área, ou baixe a similaridade mínima.")
            self._show_preview(frame, m, found=False)
            return
        cor = health_color_name(m.fill_bgr)
        extra = f"  Cor do nome agora: {cor}" + (f" ({VIDA[cor]})." if cor in VIDA else ".")
        x, y = region[0] + m.x, region[1] + m.y          # coordenadas na tela inteira
        self.test_lbl.config(text=f"Nome encontrado em ({x}, {y}) na tela, {m.w}×{m.h}px "
                                  f"(na área de busca: {m.x}, {m.y}). Similaridade {m.score:.2f}"
                                  f" (a segunda melhor: {m.second:.2f}).{extra}")
        self._show_preview(frame, m, found=True)

    def _show_preview(self, frame, m, found: bool) -> None:
        """Janela com a área de busca e um retângulo em volta do que o bot achou (à direita, o trecho ampliado)."""
        img = frame.copy()
        col = (255, 0, 255) if found else (0, 165, 255)
        cv2.rectangle(img, (m.x - 3, m.y - 3), (m.x + m.w + 2, m.y + m.h + 2), col, 2)
        if self._preview_win is not None and self._preview_win.winfo_exists():
            self._preview_win.destroy()
        win = tk.Toplevel(self)
        win.title("Onde o bot achou o nome do pokémon" if found else "Melhor candidato (abaixo do mínimo)")
        self._preview_win = win
        full = to_photo(Image.fromarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB)), 820, 520, max_scale=1)
        h, w = frame.shape[:2]
        x0, y0 = max(0, m.x - 20), max(0, m.y - 12)
        zoom = to_photo(Image.fromarray(cv2.cvtColor(img[y0:min(h, m.y + m.h + 12), x0:min(w, m.x + m.w + 20)],
                                                       cv2.COLOR_BGR2RGB)), 420, 200, max_scale=8)
        a = ttk.Label(win, image=full); a.image = full          # evita o garbage collector
        a.grid(row=0, column=0, padx=8, pady=8)
        b = ttk.Label(win, image=zoom); b.image = zoom
        b.grid(row=1, column=0, padx=8, pady=(0, 8))
