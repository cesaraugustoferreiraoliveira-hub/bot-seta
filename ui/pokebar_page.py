"""Página para configurar a leitura das barras do pokémon."""
from __future__ import annotations
import tkinter as tk
from tkinter import ttk

import cv2

from core.config import ConfigStore, POKEBAR_HEALTH_PATH, POKEBAR_SKILL_PATH
from core.magic_cut import load_template, save_template
from core.pokebar import PokeBarController, is_full
from .imgutil import over_checkerboard, to_photo
from .magic_cut_dialog import MagicCutDialog
from .region_selector import crop_region, select_region


class BarPicker(ttk.LabelFrame):
    def __init__(self, master, cfg, title, path, name):
        super().__init__(master, text=f" Sprite da barra de {title} ")
        self.cfg, self.path, self.name, self._photo = cfg, path, name, None
        self.preview = ttk.Label(self, text="(não selecionada)", relief="sunken", width=20, anchor="center")
        self.preview.grid(row=0, column=0, rowspan=2, padx=8, pady=8, ipady=14)
        ttk.Button(self, text=f"Selecionar barra de {title} na tela", command=self.pick).grid(row=0, column=1, sticky="w", padx=6, pady=(10, 3))
        ttk.Label(self, text="Inclua a barra inteira, inclusive a moldura; ela é usada para localizar a barra no pokebar space.",
                  foreground="#666", wraplength=370).grid(row=1, column=1, sticky="w", padx=6, pady=(3, 10))
        self.refresh()

    def pick(self):
        region, shot = select_region(self, f"Desenhe uma caixa em volta da barra de {self.name} inteira  •  Esc cancela")
        if not region:
            return
        dialog = MagicCutDialog(self, crop_region(shot, region))
        self.wait_window(dialog)
        if dialog.result is not None:
            save_template(dialog.result, self.path)
            self.refresh()

    def refresh(self):
        loaded = load_template(self.path)
        if loaded is None:
            return
        bgr, mask = loaded
        self._photo = to_photo(over_checkerboard(cv2.cvtColor(cv2.dstack([bgr, mask]), cv2.COLOR_BGRA2RGBA)), 190, 70, max_scale=5)
        self.preview.config(image=self._photo, text="", width=0)


class PokeBarPage(ttk.Frame):
    def __init__(self, master, cfg: ConfigStore):
        super().__init__(master)
        self.cfg = cfg
        self._photos = []
        self.health = BarPicker(self, cfg, "vida", POKEBAR_HEALTH_PATH, "vida")
        self.skill = BarPicker(self, cfg, "habilidades", POKEBAR_SKILL_PATH, "habilidades")
        self.health.grid(row=0, column=0, sticky="ew", padx=(10, 5), pady=8)
        self.skill.grid(row=0, column=1, sticky="ew", padx=(5, 10), pady=8)
        self._space()
        self._rules()

    def _space(self):
        box = ttk.LabelFrame(self, text=" Pokebar space ")
        box.grid(row=1, column=0, columnspan=2, sticky="ew", padx=10, pady=(0, 8))
        self.space_lbl = ttk.Label(box, text="não definido", width=32)
        ttk.Label(box, text="Local onde o bot procura as duas barras.", font=("Segoe UI", 9, "bold")).grid(row=0, column=0, sticky="w", padx=10, pady=(9, 0))
        ttk.Label(box, text="Selecione uma área que contenha a barra de vida e a barra de habilidades.", foreground="#666").grid(row=1, column=0, sticky="w", padx=10, pady=(0, 8))
        self.space_lbl.grid(row=0, column=1, sticky="w", padx=8)
        ttk.Button(box, text="Selecionar pokebar space", command=self._pick_space).grid(row=1, column=1, sticky="w", padx=8, pady=(0, 8))
        self._refresh_space()

    def _rules(self):
        box = ttk.LabelFrame(self, text=" Proteção de vida e validação R + E ")
        box.grid(row=2, column=0, columnspan=2, sticky="ew", padx=10, pady=(0, 8))
        self.active = tk.BooleanVar(value=bool(self.cfg.get("pokebar_ativo")))
        ttk.Checkbutton(box, text="Monitorar a vida enquanto o bot estiver rodando e apertar a tecla configurada ao atingir o limite",
                        variable=self.active, command=lambda: self.cfg.set("pokebar_ativo", bool(self.active.get()))).grid(
                            row=0, column=0, columnspan=4, sticky="w", padx=10, pady=(8, 3))
        ttk.Label(box, text="Limite de vida (%)").grid(row=1, column=0, sticky="w", padx=10)
        ttk.Label(box, text="Tecla").grid(row=1, column=1, sticky="w", padx=8)
        self.rule_rows = ttk.Frame(box); self.rule_rows.grid(row=2, column=0, columnspan=3, sticky="w", padx=10)
        ttk.Button(box, text="+ Adicionar regra", command=self._add_rule).grid(row=3, column=0, sticky="w", padx=10, pady=(3, 8))
        self.validate = tk.BooleanVar(value=bool(self.cfg.get("pokebar_validar_sequencia", True)))
        ttk.Checkbutton(box, text="Validar R/E pela barra de habilidades (R deve gastar; E deve encher completamente)",
                        variable=self.validate, command=lambda: self.cfg.set("pokebar_validar_sequencia", bool(self.validate.get()))).grid(
                            row=3, column=1, columnspan=3, sticky="w", padx=8, pady=(3, 8))
        ttk.Button(box, text="Testar leitura agora", command=self._test).grid(row=4, column=0, sticky="w", padx=10, pady=(0, 8))
        self.result = ttk.Label(box, text="", wraplength=680, justify="left")
        self.result.grid(row=4, column=1, columnspan=3, sticky="w", padx=8, pady=(0, 8))
        self.rules = [dict(v) for v in self.cfg.get("pokebar_regras_vida", [])]
        self._render_rules()

    def _pick_space(self):
        region, _ = select_region(self, "Desenhe uma caixa que contenha as duas barras  •  Esc cancela")
        if region:
            self.cfg.set("regiao_pokebar", region); self._refresh_space()

    def _refresh_space(self):
        r = self.cfg.get("regiao_pokebar")
        self.space_lbl.config(text="não definido" if not r else f"x={r[0]}  y={r[1]}  {r[2]}×{r[3]}")

    def _save_rules(self): self.cfg.set("pokebar_regras_vida", [dict(r) for r in self.rules])
    def _add_rule(self):
        self.rules.append({"percentual": 30, "tecla": ""}); self._save_rules(); self._render_rules()
    def _remove_rule(self, i):
        del self.rules[i]; self._save_rules(); self._render_rules()
    def _render_rules(self):
        for w in self.rule_rows.winfo_children(): w.destroy()
        if not self.rules:
            ttk.Label(self.rule_rows, text="(nenhuma regra — adicione uma para evitar que o pokémon morra)", foreground="#666").grid(row=0, column=0, columnspan=3)
        for i, rule in enumerate(self.rules):
            pct, key = tk.StringVar(value=str(rule.get("percentual", 30))), tk.StringVar(value=str(rule.get("tecla", "")))
            ttk.Spinbox(self.rule_rows, from_=1, to=100, width=6, textvariable=pct).grid(row=i, column=0, pady=2)
            ttk.Entry(self.rule_rows, width=12, textvariable=key).grid(row=i, column=1, padx=8, pady=2)
            ttk.Button(self.rule_rows, text="Remover", command=lambda i=i: self._remove_rule(i)).grid(row=i, column=2, pady=2)
            def save(*_, i=i, pct=pct, key=key):
                try: value = max(1, min(100, int(float(pct.get().replace(',', '.')))))
                except ValueError: return
                self.rules[i] = {"percentual": value, "tecla": key.get().strip().lower()}; self._save_rules()
            pct.trace_add("write", save); key.trace_add("write", save)

    def _test(self):
        ctl = PokeBarController(self.cfg)
        if not ctl.configured:
            self.result.config(text="Defina o pokebar space primeiro."); return
        health, skill = ctl.read()
        def show(label, value):
            return f"{label}: não localizada" if value is None else f"{label}: {value:.1f}%" + (" (CHEIA)" if is_full(value) else "")
        if health is not None and skill is not None:
            suffix = "Barras confirmadas."
        elif health is not None or skill is not None:
            suffix = "Leitura parcial: confirme a outra barra selecionando sua sprite novamente."
        else:
            suffix = "Nenhuma barra localizada: selecione as sprites novamente ou diminua a similaridade no config."
        self.result.config(text=show("Vida", health) + "  |  " + show("Habilidades", skill) + ". " + suffix)
