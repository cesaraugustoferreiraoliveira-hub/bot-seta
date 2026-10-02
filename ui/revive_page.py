"""Página de configuração do revive, reutilizando seletor de regiões e recorte mágico."""
from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk

import cv2
import numpy as np

from core import capture, vision
from core.config import ConfigStore, LIFE_BAR_PATH
from core.magic_cut import load_template, save_template
from core.revive import ImageChangeMonitor, life_bar_percentage
from .areas_panel import AreaRow
from .imgutil import over_checkerboard, to_photo
from .magic_cut_dialog import MagicCutDialog
from .region_selector import crop_region, select_region


class RevivePage(ttk.Frame):
    def __init__(self, master, cfg: ConfigStore):
        super().__init__(master)
        self.cfg, self._photo = cfg, None
        self._build_life(); self._build_revive(); self._build_skills()

    def _build_life(self):
        box = ttk.LabelFrame(self, text=" Life Bar ")
        box.grid(row=0, column=0, sticky="ew", padx=10, pady=(8, 4))
        self.preview = ttk.Label(box, text="(Sprite Life Bar não configurada)", relief="sunken", width=30)
        self.preview.grid(row=0, column=0, rowspan=3, padx=10, pady=8, ipady=15)
        ttk.Button(box, text="Selecionar Sprite Life Bar", command=self.pick_sprite).grid(row=0, column=1, sticky="w", pady=(10, 2))
        ttk.Button(box, text="Testar leitura da vida", command=self.test_life).grid(row=1, column=1, sticky="w", pady=2)
        self.life_status = ttk.Label(box, text="", wraplength=430, justify="left")
        self.life_status.grid(row=2, column=1, sticky="w", pady=(2, 10))
        area = ttk.Frame(box); area.grid(row=3, column=0, columnspan=2, sticky="w")
        self.life_region = AreaRow(area, 0, self.cfg, "regiao_life_bar", "Local Life Bar",
                                   "Região onde o bot procura a Sprite Life Bar")
        self.refresh_sprite()

    def pick_sprite(self):
        region, shot = select_region(self, "Desenhe uma caixa que contenha a Life Bar  •  Esc cancela")
        if not region:
            return
        dlg = MagicCutDialog(self, crop_region(shot, region)); self.wait_window(dlg)
        if dlg.result is not None:
            save_template(dlg.result, LIFE_BAR_PATH); self.refresh_sprite()

    def refresh_sprite(self):
        loaded = load_template(LIFE_BAR_PATH)
        if loaded is None:
            return
        bgr, mask = loaded
        rgba = cv2.cvtColor(np.dstack([bgr, mask]), cv2.COLOR_BGRA2RGBA)
        self._photo = to_photo(over_checkerboard(rgba), 150, 90)
        self.preview.config(image=self._photo, text="", width=0)

    def test_life(self):
        region = self.cfg.get("regiao_life_bar")
        tmpl = vision.SpriteTemplate.from_file(LIFE_BAR_PATH)
        if not region or tmpl is None:
            self.life_status.config(text="Configure a Sprite Life Bar e o Local Life Bar primeiro."); return
        life, score = life_bar_percentage(capture.grab(region), tmpl, float(self.cfg.get("life_bar_limiar", .85)))
        self.life_status.config(text=(f"Vida estimada: {life:.1f}% (similaridade {score:.2f})" if life is not None
                                     else f"Life Bar não encontrada (melhor similaridade {score:.2f})."))

    def _build_revive(self):
        box = ttk.LabelFrame(self, text=" Revive ")
        box.grid(row=1, column=0, sticky="ew", padx=10, pady=4)
        active = tk.BooleanVar(value=bool(self.cfg.get("revive_ativo")))
        ttk.Checkbutton(box, text="Ativar revive automático quando o Shooter concluir", variable=active,
                        command=lambda: self.cfg.set("revive_ativo", bool(active.get()))).grid(row=0, column=0, columnspan=3, sticky="w", padx=10, pady=(8, 4))
        ttk.Label(box, text="Tecla do Revive").grid(row=1, column=0, sticky="w", padx=10, pady=4)
        key = tk.StringVar(value=str(self.cfg.get("revive_tecla") or "e"))
        ent = ttk.Entry(box, textvariable=key, width=10); ent.grid(row=1, column=1, sticky="w", pady=4)
        key.trace_add("write", lambda *_: self.cfg.set("revive_tecla", key.get().strip().lower()))
        ttk.Label(box, text="A tecla fica pressionada até a mudança visual confirmada.", foreground="#666").grid(row=1, column=2, sticky="w", padx=8)
        self.monitor_region = AreaRow(box, 2, self.cfg, "regiao_revive_monitor", "Região para confirmar o Revive",
                                      "Ao definir, uma imagem de referência é capturada e persistida",
                                      on_selected=lambda region: ImageChangeMonitor().capture_reference(region))
        ttk.Button(box, text="Capturar/atualizar referência agora", command=self.capture_reference).grid(row=6, column=1, sticky="w", padx=6, pady=(0, 8))
        ttk.Label(box, text="Mudança mínima").grid(row=6, column=0, sticky="w", padx=10)
        threshold = tk.DoubleVar(value=float(self.cfg.get("revive_diferenca_pct", 4)))
        ttk.Spinbox(box, from_=0.1, to=100, increment=.5, width=6, textvariable=threshold).grid(row=6, column=0, sticky="e", padx=(0, 4))
        threshold.trace_add("write", lambda *_: self._save_threshold(threshold))

    def _save_threshold(self, var):
        try:
            value = float(var.get())
            if .1 <= value <= 100: self.cfg.set("revive_diferenca_pct", value)
        except tk.TclError:
            pass

    def capture_reference(self):
        region = self.cfg.get("regiao_revive_monitor")
        if not region:
            messagebox.showwarning("Revive", "Defina a região de monitoramento primeiro.", parent=self); return
        ImageChangeMonitor().capture_reference(region)
        messagebox.showinfo("Revive", "Imagem de referência atualizada.", parent=self)

    def _build_skills(self):
        box = ttk.LabelFrame(self, text=" Habilidades por vida ")
        box.grid(row=2, column=0, sticky="ew", padx=10, pady=(4, 8))
        ttk.Label(box, text="Cada habilidade dispara uma vez ao cruzar o limite; ela é rearmada quando a vida sobe.", foreground="#666").grid(row=0, column=0, columnspan=4, sticky="w", padx=10, pady=(7, 2))
        self.skill_rows = ttk.Frame(box); self.skill_rows.grid(row=1, column=0, columnspan=4, sticky="w", padx=10)
        ttk.Button(box, text="+ Adicionar habilidade", command=self.add_skill).grid(row=2, column=0, sticky="w", padx=10, pady=(3, 8))
        self.skills = [dict(x) for x in (cfg.get("revive_habilidades") or [])]
        self.render_skills()

    def add_skill(self):
        self.skills.append({"vida": 80, "tecla": ""}); self.save_skills(); self.render_skills()

    def save_skills(self):
        # Só persiste regras válidas; a linha permanece visível para o usuário corrigir.
        valid, seen = [], set()
        for rule in self.skills:
            try: life = float(rule.get("vida"))
            except (TypeError, ValueError): continue
            key = str(rule.get("tecla") or "").strip().lower()
            if key and 0 <= life <= 100 and life not in seen:
                valid.append({"vida": life, "tecla": key}); seen.add(life)
        self.cfg.set("revive_habilidades", valid)

    def render_skills(self):
        for child in self.skill_rows.winfo_children(): child.destroy()
        for i, rule in enumerate(self.skills):
            ttk.Label(self.skill_rows, text=f"{i + 1}.").grid(row=i, column=0, padx=(0, 5), pady=2)
            value = tk.StringVar(value=str(rule.get("vida", 80)))
            ttk.Spinbox(self.skill_rows, from_=0, to=100, width=5, textvariable=value).grid(row=i, column=1)
            ttk.Label(self.skill_rows, text="% ou menos → tecla").grid(row=i, column=2, padx=5)
            key = tk.StringVar(value=str(rule.get("tecla") or "")); ttk.Entry(self.skill_rows, width=8, textvariable=key).grid(row=i, column=3)
            def sync(*_, i=i, value=value, key=key):
                self.skills[i] = {"vida": value.get(), "tecla": key.get()}; self.save_skills()
            value.trace_add("write", sync); key.trace_add("write", sync)
            ttk.Button(self.skill_rows, text="Remover", command=lambda i=i: self.remove_skill(i)).grid(row=i, column=4, padx=6)

    def remove_skill(self, i):
        del self.skills[i]; self.save_skills(); self.render_skills()
