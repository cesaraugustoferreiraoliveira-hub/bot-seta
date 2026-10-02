"""Módulo da GUI: Mapa completo (captura da imagem + definição de corredor/obstáculo + prévia da rota)."""
from __future__ import annotations
import tkinter as tk
from tkinter import ttk

import cv2
import numpy as np
from PIL import Image

from core import mapping
from core.config import ConfigStore, MAP_PATH, MAPMASK_PATH
from core.mapmask import MapMask
from .imgutil import to_photo
from .map_mask_dialog import MapMaskDialog
from .region_selector import crop_region, select_region

SENTIDOS = {"horario": "horário", "antihorario": "anti-horário"}


class FullMapPanel(ttk.LabelFrame):
    def __init__(self, master, cfg: ConfigStore):
        super().__init__(master, text=" Mapa completo ")
        self.cfg = cfg
        self._photo = None

        ttk.Label(self, text="Mapa completo", font=("Segoe UI", 9, "bold")).grid(
            row=0, column=0, sticky="w", padx=10, pady=(8, 0))
        ttk.Label(self, text="Imagem do mapa inteiro + o que é corredor\ne o que é obstáculo",
                  foreground="#666", justify="left").grid(row=1, column=0, sticky="nw", padx=10)

        self.info = ttk.Label(self, text="", justify="left", width=34)
        self.info.grid(row=0, column=1, sticky="w", padx=6, pady=(8, 0))
        btns = ttk.Frame(self)
        btns.grid(row=1, column=1, sticky="w", padx=6, pady=(0, 4))
        ttk.Button(btns, text="Capturar mapa completo", command=self.capture_map).pack(anchor="w")
        self.edit_btn = ttk.Button(btns, text="Definir obstáculos e corredor", command=self.edit_mask)
        self.edit_btn.pack(anchor="w", pady=(4, 0))

        self.prev = ttk.Label(self, text="sem prévia", anchor="center", relief="sunken", width=34)
        self.prev.grid(row=0, column=2, rowspan=2, padx=10, pady=6, ipady=12)
        self.route_lbl = ttk.Label(self, text="", foreground="#666")
        self.route_lbl.grid(row=2, column=0, columnspan=3, sticky="w", padx=10, pady=(0, 8))
        self.refresh()

    # ---------------------------------------------------------------- ações
    def capture_map(self) -> None:
        region, shot = select_region(self, "Selecione o MAPA COMPLETO inteiro  •  Esc cancela")
        if not region:
            return
        crop = crop_region(shot, region)
        bgr = cv2.cvtColor(np.array(crop.convert("RGB")), cv2.COLOR_RGB2BGR)
        old = MapMask.load(MAP_PATH, MAPMASK_PATH)
        cv2.imwrite(str(MAP_PATH), bgr)
        self.cfg.set("regiao_mapa_completo", region)
        if old is not None and old.bgr.shape != bgr.shape and MAPMASK_PATH.exists():
            MAPMASK_PATH.unlink()  # tamanho diferente: as marcações antigas não servem mais
        self.refresh()

    def edit_mask(self) -> None:
        mm = MapMask.load(MAP_PATH, MAPMASK_PATH)
        if mm is None:
            return
        dlg = MapMaskDialog(self, mm, int(self.cfg["tolerancia_cor"]))
        self.wait_window(dlg)
        if dlg.saved:
            self.refresh()

    # ---------------------------------------------------------------- exibição
    def refresh(self) -> None:
        mm = MapMask.load(MAP_PATH, MAPMASK_PATH)
        if mm is None:
            self.info.config(text="não capturado")
            self.prev.config(image="", text="sem prévia")
            self.edit_btn.state(["disabled"])
            self.route_lbl.config(text="Capture o mapa completo para começar.")
            return
        self.edit_btn.state(["!disabled"])
        h, w = mm.bgr.shape[:2]
        st = mm.stats()
        self.info.config(text=f"{w}×{h} px\ncorredor {st['corredor']:.0%}  •  obstáculo {st['obstaculo']:.0%}  •  "
                              f"sem marcar {st['sem_marcar']:.0%}")
        loop = None
        walk = mm.walkable()
        if not walk.any():
            self.route_lbl.config(text="Defina o corredor (Definir obstáculos e corredor) para gerar a rota.")
        else:
            try:
                loop = mapping.build_loop(walk, self.cfg["sentido"])
                self.route_lbl.config(text=f"Rota: {len(loop)} pontos, sentido {SENTIDOS.get(self.cfg['sentido'], '')} "
                                           "(linha amarela; a seta branca mostra a direção).")
            except (ValueError, ZeroDivisionError) as exc:
                self.route_lbl.config(text=f"Rota: {exc}")
        k = int(max(1, min(6, 230 / w, 150 / h)))
        img = mm.overlay(loop, scale=k)
        self._photo = to_photo(Image.fromarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB)), 230, 150, max_scale=1)
        self.prev.config(image=self._photo, text="", width=0)
