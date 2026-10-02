"""Módulo da GUI: Sprite da seta (recorte mágico, igual à sprite de parada) + teste de detecção."""
from __future__ import annotations
import tkinter as tk
from tkinter import ttk

import cv2
import numpy as np
from PIL import Image

from core import capture, vision
from core.config import ARROW_PATH, ConfigStore, MAP_PATH, MAPMASK_PATH
from core.magic_cut import load_template, save_template
from core.locator import MapLocator
from core.mapmask import MapMask
from .imgutil import over_checkerboard, to_photo
from .magic_cut_dialog import MagicCutDialog
from .region_selector import crop_region, select_region


class ArrowPanel(ttk.LabelFrame):
    def __init__(self, master, cfg: ConfigStore):
        super().__init__(master, text=" Sprite da seta ")
        self.cfg = cfg
        self._photo = None

        self.preview = ttk.Label(self, text="(nenhuma seta)", anchor="center", relief="sunken", width=16)
        self.preview.grid(row=0, column=0, rowspan=4, padx=10, pady=8, ipady=18)

        ttk.Button(self, text="Selecionar seta na tela", command=self.pick_arrow).grid(
            row=0, column=1, sticky="w", padx=6, pady=(10, 2))

        srow = ttk.Frame(self)
        srow.grid(row=1, column=1, sticky="w", padx=6, pady=2)
        ttk.Label(srow, text="Similaridade mínima").pack(side="left")
        self.lim = tk.DoubleVar(value=float(cfg["seta_limiar"]))
        ttk.Spinbox(srow, from_=0.5, to=1.0, increment=0.01, format="%.2f", width=5,
                    textvariable=self.lim).pack(side="left", padx=6)
        self.lim.trace_add("write", lambda *a: self._save_lim())

        lrow = ttk.Frame(self)
        lrow.grid(row=2, column=1, sticky="w", padx=6, pady=2)
        ttk.Label(lrow, text="Posição no mapa: mín.").pack(side="left")
        self.loc_lim = tk.DoubleVar(value=float(cfg["loc_limiar"]))
        ttk.Spinbox(lrow, from_=0.2, to=1.0, increment=0.01, format="%.2f", width=5,
                    textvariable=self.loc_lim).pack(side="left", padx=6)
        self.loc_lim.trace_add("write", lambda *a: self._save_loc_lim())

        ttk.Button(self, text="Testar seta e posição no mapa", command=self.test_detect).grid(
            row=3, column=1, sticky="w", padx=6, pady=(2, 10))
        self.test_lbl = ttk.Label(self, text="", wraplength=320, justify="left")
        self.test_lbl.grid(row=4, column=0, columnspan=2, sticky="w", padx=10, pady=(0, 8))
        self._preview_win = None
        self.refresh()

    # ---------------------------------------------------------------- ações
    def _save_lim(self) -> None:
        try:
            self.cfg.set("seta_limiar", round(float(self.lim.get()), 2))
        except (tk.TclError, ValueError):
            pass  # campo vazio durante a digitação

    def _save_loc_lim(self) -> None:
        try:
            self.cfg.set("loc_limiar", round(float(self.loc_lim.get()), 2))
        except (tk.TclError, ValueError):
            pass

    def pick_arrow(self) -> None:
        region, shot = select_region(
            self, "Desenhe uma caixa pequena que CONTENHA a seta (com um pouco de fundo em volta)  •  Esc cancela")
        if not region:
            return
        dlg = MagicCutDialog(self, crop_region(shot, region))
        self.wait_window(dlg)
        if dlg.result is not None:
            save_template(dlg.result, ARROW_PATH)
            self.refresh()
            self.test_lbl.config(text="")

    def refresh(self) -> None:
        loaded = load_template(ARROW_PATH)
        if loaded is None:
            return
        bgr, mask = loaded
        rgba = cv2.cvtColor(np.dstack([bgr, mask]), cv2.COLOR_BGRA2RGBA)
        self._photo = to_photo(over_checkerboard(rgba), 110, 90)
        self.preview.config(image=self._photo, text="", width=0)

    def test_detect(self) -> None:
        """Acha a seta no mapa ao vivo e depois localiza o mapa ao vivo dentro do mapa completo."""
        region = self.cfg.get("regiao_mapa")
        if not region:
            self.test_lbl.config(text="Defina a área 'Mapa onde a seta está' primeiro.")
            return
        frame = capture.grab(region)
        h, w = frame.shape[:2]
        tmpl = vision.SpriteTemplate.from_file(ARROW_PATH)
        pos, method, score = vision.locate_arrow(frame, tmpl, self.cfg["seta_limiar"], self.cfg["limiar_seta"])
        if pos is None:
            if method == "sprite":
                self.test_lbl.config(text=f"Seta NÃO encontrada (melhor similaridade {score:.2f}, mínimo "
                                          f"{self.cfg['seta_limiar']:.2f}). Baixe a similaridade mínima ou "
                                          "selecione a seta de novo.")
            else:
                self.test_lbl.config(text="Seta NÃO encontrada. Selecione a sprite da seta acima.")
            return
        txt = f"Seta no mapa ao vivo: ({pos[0]:.0f}, {pos[1]:.0f}) de {w}×{h}"
        txt += f" (similaridade {score:.2f})." if method == "sprite" else " (por pixels brancos; sem sprite da seta)."
        mm = MapMask.load(MAP_PATH, MAPMASK_PATH)
        if mm is None:
            self.test_lbl.config(text=txt + "\nCapture o mapa completo para testar a posição.")
            return
        self.test_lbl.config(text=txt + "\nProcurando o mapa ao vivo dentro do mapa completo…")
        self.update_idletasks()
        loc = MapLocator(mm.bgr, self.cfg["loc_limiar"], self.cfg.get("escala_mapa") or self.cfg.get("escala_mapa_calibrada"),
                         int(self.cfg.get("loc_raio", 48)))
        if loc.scale is None:
            loc.calibrate(frame, pos)  # ~1 s: descobre o zoom entre os dois mapas
            if loc.scale and loc.cal_score >= 0.5 and loc.cal_margin >= 0.25:
                self.cfg.set("escala_mapa_calibrada", round(loc.scale, 4))  # o bot reaproveita ao ligar
        fix = loc.locate(frame, pos) if loc.scale else None
        if fix is None and loc.scale and self.cfg.get("escala_mapa_calibrada") and not self.cfg.get("escala_mapa"):
            loc.scale = None                     # a escala salva não serviu: recalibra uma vez
            loc.calibrate(frame, pos)
            fix = loc.locate(frame, pos) if loc.scale else None
        if fix is None:
            best = max(loc.cal_score, loc.last_score)
            self.test_lbl.config(text=txt + f"\nNÃO achei o mapa ao vivo dentro do mapa completo (melhor similaridade "
                                            f"{best:.2f}, mínimo {self.cfg['loc_limiar']}). Os dois mapas precisam mostrar "
                                            "a mesma região com as mesmas cores.")
            return
        fh, fw = mm.state.shape
        ix, iy = int(round(fix[0])), int(round(fix[1]))
        on = "corredor" if mm.walkable()[min(max(iy, 0), fh - 1), min(max(ix, 0), fw - 1)] else "OBSTÁCULO"
        self.test_lbl.config(text=txt + f"\nNo mapa completo: ({ix}, {iy}), sobre {on}. Escala {loc.scale:.3f}, "
                                        f"similaridade {loc.last_score:.2f}.")
        self._show_preview(mm, loc, fix, (w, h))

    def _show_preview(self, mm: MapMask, loc: MapLocator, fix, live_size) -> None:
        """Janela com o mapa completo, o retângulo que o mapa ao vivo cobre (amarelo) e a seta (magenta)."""
        img = mm.overlay(None, 1)
        half = int(round((loc.patch_r + 0.5) * loc.scale))   # o bot compara só um recorte ao redor da seta
        cv2.rectangle(img, (int(fix[0]) - half, int(fix[1]) - half), (int(fix[0]) + half, int(fix[1]) + half), (0, 255, 255), 1)
        cv2.circle(img, (int(round(fix[0])), int(round(fix[1]))), 4, (255, 0, 255), -1)
        if self._preview_win is not None and self._preview_win.winfo_exists():
            self._preview_win.destroy()
        win = tk.Toplevel(self)
        win.title("Onde o bot acha que a seta está")
        self._preview_win = win
        photo = to_photo(Image.fromarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB)), 760, 620, max_scale=2)
        lbl = ttk.Label(win, image=photo)
        lbl.image = photo  # evita o garbage collector
        lbl.pack(padx=8, pady=(8, 2))
        ttk.Label(win, text="Retângulo amarelo = recorte ao redor da seta usado na comparação • Ponto magenta = a seta  "
                            "(verde = corredor, vermelho = obstáculo)", foreground="#555").pack(pady=(0, 8))
