"""Página de configuração e teste visual da Pokébar."""
from __future__ import annotations
import tkinter as tk
from tkinter import ttk

import cv2
import numpy as np

from core import capture
from core.config import POKEBAR_ABILITY_PATH, POKEBAR_LIFE_PATH, ConfigStore
from core.pokebar import PokebarMonitor
from .imgutil import to_photo
from .region_selector import crop_region, select_region


class PokebarPage(ttk.Frame):
    def __init__(self, master, cfg: ConfigStore):
        super().__init__(master)
        self.cfg, self._photos = cfg, {}
        box = ttk.LabelFrame(self, text=" Pokébar Space ")
        box.grid(row=0, column=0, sticky="ew", padx=10, pady=(8, 4))
        ttk.Label(box, text="Área da tela onde o bot procura as duas barras.").grid(row=0, column=0, sticky="w", padx=8, pady=6)
        self.space_label = ttk.Label(box)
        self.space_label.grid(row=0, column=1, padx=8)
        ttk.Button(box, text="Selecionar Pokébar Space", command=self.pick_space).grid(row=0, column=2, padx=8, pady=6)

        bars = ttk.LabelFrame(self, text=" Barras (capture-as CHEIAS para criar a referência de 100%) ")
        bars.grid(row=1, column=0, sticky="ew", padx=10, pady=4)
        self.life = self._bar_row(bars, 0, "life", "Pokébar Life Bar", POKEBAR_LIFE_PATH)
        self.ability = self._bar_row(bars, 1, "ability", "Pokébar Ability Bar", POKEBAR_ABILITY_PATH)

        rules = ttk.LabelFrame(self, text=" Ações pela Life Bar ")
        rules.grid(row=2, column=0, sticky="ew", padx=10, pady=4)
        ttk.Label(rules, text="Ações ficam salvas para execução pela automação quando a vida estiver abaixo do percentual.", foreground="#666").grid(row=0, column=0, columnspan=4, sticky="w", padx=8, pady=(6, 2))
        self.rules = [dict(x) for x in cfg.get("pokebar_life_actions", [])]
        self.rule_rows = ttk.Frame(rules); self.rule_rows.grid(row=1, column=0, columnspan=4, sticky="w", padx=8)
        ttk.Button(rules, text="+ Adicionar ação", command=self.add_rule).grid(row=2, column=0, sticky="w", padx=8, pady=(2, 6))
        self.render_rules()

        valid = ttk.LabelFrame(self, text=" Validação visual do Shooter ")
        valid.grid(row=3, column=0, sticky="ew", padx=10, pady=(4, 8))
        self.enabled = tk.BooleanVar(value=bool(cfg.get("pokebar_validar_shooter")))
        ttk.Checkbutton(valid, text="Confirmar R/E pela Ability Bar antes de considerar o comando concluído", variable=self.enabled,
                        command=lambda: cfg.set("pokebar_validar_shooter", bool(self.enabled.get()))).grid(row=0, column=0, columnspan=4, sticky="w", padx=8, pady=(6, 2))
        self._number(valid, 1, "Tempo para confirmar", "pokebar_validar_timeout_s", 0.2, 10, 0.1, "s")
        self._number(valid, 2, "Queda mínima após R", "pokebar_validar_delta_pct", 1, 100, 1, "%")
        ttk.Button(valid, text="Testar leitura agora", command=self.test).grid(row=3, column=0, padx=8, pady=(4, 6), sticky="w")
        self.status = ttk.Label(valid, text="", wraplength=640); self.status.grid(row=3, column=1, columnspan=3, sticky="w")
        self.refresh()

    def _bar_row(self, parent, row, kind, title, path):
        ttk.Label(parent, text=title, font=("Segoe UI", 10, "bold")).grid(row=row, column=0, padx=8, pady=6, sticky="w")
        label = ttk.Label(parent, text="não capturada", width=28)
        label.grid(row=row, column=1, sticky="w")
        ttk.Button(parent, text="Capturar barra na tela", command=lambda: self.pick_bar(kind, path)).grid(row=row, column=2, padx=8)
        preview = ttk.Label(parent, text="sem referência", relief="sunken", width=18)
        preview.grid(row=row, column=3, padx=8, pady=5)
        return label, preview

    def pick_space(self):
        region, _ = select_region(self, "Selecione a área que contém Life Bar e Ability Bar  •  Esc cancela")
        if region: self.cfg.set("pokebar_space", region); self.refresh()

    def pick_bar(self, kind, path):
        space = self.cfg.get("pokebar_space")
        if not space:
            self.status.config(text="Defina o Pokébar Space primeiro."); return
        region, shot = select_region(self, f"Selecione a {kind.title()} Bar CHEIA dentro do Pokébar Space  •  Esc cancela")
        if not region: return
        sx, sy, sw, sh = space; x, y, w, h = region
        if x < sx or y < sy or x + w > sx + sw or y + h > sy + sh:
            self.status.config(text="A barra precisa ficar inteiramente dentro do Pokébar Space."); return
        cv2.imwrite(str(path), cv2.cvtColor(np.array(crop_region(shot, region)), cv2.COLOR_RGB2BGR))
        self.cfg.set(f"pokebar_{kind}_region", [x - sx, y - sy, w, h]); self.refresh()

    def _number(self, parent, row, label, key, lo, hi, inc, unit):
        ttk.Label(parent, text=label).grid(row=row, column=0, padx=8, sticky="w")
        var = tk.StringVar(value=str(self.cfg.get(key)))
        ttk.Spinbox(parent, from_=lo, to=hi, increment=inc, width=7, textvariable=var).grid(row=row, column=1, sticky="w")
        ttk.Label(parent, text=unit).grid(row=row, column=2, sticky="w")
        var.trace_add("write", lambda *_: self._save_number(key, var, lo, hi))

    def _save_number(self, key, var, lo, hi):
        try: value = float(var.get().replace(",", "."))
        except ValueError: return
        if lo <= value <= hi: self.cfg.set(key, value)

    def add_rule(self): self.rules.append({"percentual": 70, "comando": ""}); self.save_rules(); self.render_rules()
    def save_rules(self): self.cfg.set("pokebar_life_actions", [dict(x) for x in self.rules])
    def render_rules(self):
        for child in self.rule_rows.winfo_children(): child.destroy()
        for i, rule in enumerate(self.rules):
            ttk.Label(self.rule_rows, text="Vida abaixo de").grid(row=i, column=0, pady=2)
            pct = tk.StringVar(value=str(rule.get("percentual", 70))); ttk.Spinbox(self.rule_rows, from_=0, to=100, width=5, textvariable=pct).grid(row=i, column=1)
            ttk.Label(self.rule_rows, text="%: comando").grid(row=i, column=2, padx=(4, 2))
            cmd = tk.StringVar(value=rule.get("comando", "")); ttk.Entry(self.rule_rows, width=28, textvariable=cmd).grid(row=i, column=3)
            ttk.Button(self.rule_rows, text="Remover", command=lambda i=i: (self.rules.pop(i), self.save_rules(), self.render_rules())).grid(row=i, column=4, padx=6)
            pct.trace_add("write", lambda *_, i=i, v=pct: self._rule_pct(i, v)); cmd.trace_add("write", lambda *_, i=i, v=cmd: self._rule_cmd(i, v))

    def _rule_pct(self, i, v):
        try: self.rules[i]["percentual"] = max(0, min(100, int(float(v.get())))); self.save_rules()
        except ValueError: pass
    def _rule_cmd(self, i, v): self.rules[i]["comando"] = v.get(); self.save_rules()

    def refresh(self):
        space = self.cfg.get("pokebar_space"); self.space_label.config(text="não definido" if not space else f"x={space[0]} y={space[1]} {space[2]}×{space[3]}")
        for kind, path, widgets in (("life", POKEBAR_LIFE_PATH, self.life), ("ability", POKEBAR_ABILITY_PATH, self.ability)):
            label, preview = widgets; region = self.cfg.get(f"pokebar_{kind}_region")
            label.config(text="não capturada" if not region else f"relativa: x={region[0]} y={region[1]} {region[2]}×{region[3]}")
            image = cv2.imread(str(path))
            if image is not None:
                self._photos[kind] = to_photo(cv2.cvtColor(image, cv2.COLOR_BGR2RGB), 160, 50, max_scale=4); preview.config(image=self._photos[kind], text="", width=0)

    def test(self):
        monitor = PokebarMonitor(self.cfg); life, ability = monitor.read("life"), monitor.read("ability")
        def txt(name, value): return f"{name}: {value.percent:.1f}%" if value else f"{name}: não configurada ou fora da área"
        self.status.config(text=txt("Life", life) + " | " + txt("Ability", ability))
