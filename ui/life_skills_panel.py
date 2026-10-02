"""Módulo da GUI: habilidades por % de vida — 'quando a vida for ≤ X%, aperte a tecla T' (quantas regras quiser).

Vale a regra de MENOR limite que ainda cobre a vida atual (ex.: regras ≤70% → 1 e ≤30% → 2: com 50% aperta 1, com 20%
aperta 2). Cada regra tem um intervalo mínimo entre apertos. A lógica fica em core/revive.py (LifeSkillMonitor).
"""
from __future__ import annotations
import tkinter as tk
from tkinter import ttk

from core import capture
from core.config import ConfigStore, LIFEBAR_PATH
from core.lifebar import LifeBarReader
from core.vision import SpriteTemplate
from .fields import key_label, number_row
from .key_capture import KeyCapture


class LifeSkillsPanel(ttk.LabelFrame):
    def __init__(self, master, cfg: ConfigStore, keys: KeyCapture):
        super().__init__(master, text=" Habilidades por % de vida do pokémon ")
        self.cfg, self.keys = cfg, keys
        self.columnconfigure(2, weight=1)

        self.ativo = tk.BooleanVar(value=bool(cfg.get("vida_hab_ativo")))
        ttk.Checkbutton(self, text="Usar habilidades de acordo com a % de vida (lida pela sprite life bar)",
                        variable=self.ativo, command=lambda: cfg.set("vida_hab_ativo", bool(self.ativo.get()))
                        ).grid(row=0, column=0, columnspan=3, sticky="w", padx=10, pady=(8, 2))
        self.so_parado = tk.BooleanVar(value=bool(cfg.get("vida_somente_parado", True)))
        ttk.Checkbutton(self, text="Só com o bot parado por excesso de sprites (em combate)", variable=self.so_parado,
                        command=lambda: cfg.set("vida_somente_parado", bool(self.so_parado.get()))
                        ).grid(row=1, column=0, columnspan=3, sticky="w", padx=10, pady=(0, 4))
        number_row(self, 2, cfg, "Ler a vida a cada", "vida_intervalo_s", 0.1, 10, 0.1, "s",
                   "intervalo entre leituras da barra de vida", float)

        box = ttk.LabelFrame(self, text=" Regras: quando a vida for menor ou igual a… ")
        box.grid(row=3, column=0, columnspan=3, sticky="ew", padx=10, pady=(8, 2))
        self.rows = ttk.Frame(box)
        self.rows.grid(row=0, column=0, sticky="w", padx=8, pady=(4, 0))
        ttk.Button(box, text="+ Adicionar habilidade", command=self._add).grid(row=1, column=0, sticky="w", padx=8, pady=(2, 8))
        ttk.Label(box, text="Vale a regra de MENOR % que ainda cobre a vida atual. O intervalo é o mínimo entre dois "
                            "apertos da mesma regra.", foreground="#666").grid(row=2, column=0, sticky="w", padx=8, pady=(0, 6))
        self._skills = [dict(s) for s in (cfg.get("vida_hab_lista") or [])]
        self._render()

        ttk.Button(self, text="Ler a vida agora", command=self.read_now).grid(row=4, column=0, columnspan=2, sticky="w", padx=10, pady=(6, 2))
        self.test_lbl = ttk.Label(self, text="", wraplength=640, justify="left")
        self.test_lbl.grid(row=5, column=0, columnspan=3, sticky="w", padx=10, pady=(2, 8))

    # ---------------------------------------------------------------- lista de regras
    def _save(self) -> None:
        self.cfg.set("vida_hab_lista", [dict(s) for s in self._skills])

    def _add(self) -> None:
        self._skills.append({"pct": 50, "tecla": "", "cooldown_s": 3.0})
        self._save()
        self._render()

    def _remove(self, i: int) -> None:
        del self._skills[i]
        self._save()
        self._render()

    def _set_key(self, i: int, k: str) -> None:
        self._skills[i]["tecla"] = k
        self._save()

    def _render(self) -> None:
        for w in self.rows.winfo_children():
            w.destroy()
        if not self._skills:
            ttk.Label(self.rows, text="(nenhuma habilidade — use '+ Adicionar habilidade')", foreground="#666").grid(row=0, column=0)
        for i, sk in enumerate(self._skills):
            ttk.Label(self.rows, text=f"{i + 1}.  vida ≤").grid(row=i, column=0, padx=(0, 4), pady=2)
            pct = tk.StringVar(value=f"{sk.get('pct', 50):g}")
            ttk.Spinbox(self.rows, from_=0, to=100, increment=5, width=5, textvariable=pct).grid(row=i, column=1)
            ttk.Label(self.rows, text="%   aperte").grid(row=i, column=2, padx=6)
            lbl = key_label(self.rows, sk.get("tecla"), 6)
            lbl.grid(row=i, column=3)
            btn = ttk.Button(self.rows, text="Alterar")
            btn.grid(row=i, column=4, padx=6)
            btn.config(command=lambda i=i, lbl=lbl, btn=btn: (lbl.config(text="APERTE…"), self.keys.capture(
                lambda k, i=i: self._set_key(i, k), btn, self._render, on_error=lambda m: self.test_lbl.config(text=m))))
            cd = tk.StringVar(value=f"{sk.get('cooldown_s', 3.0):g}")
            ttk.Label(self.rows, text="intervalo").grid(row=i, column=5, padx=(8, 4))
            ttk.Spinbox(self.rows, from_=0, to=600, increment=0.5, width=6, textvariable=cd).grid(row=i, column=6)
            ttk.Label(self.rows, text="s").grid(row=i, column=7, padx=(4, 8))
            ttk.Button(self.rows, text="Remover", command=lambda i=i: self._remove(i)).grid(row=i, column=8)

            def save(*_, i=i, pct=pct, cd=cd):
                try:
                    p, c = float(pct.get().replace(",", ".")), float(cd.get().replace(",", "."))
                except ValueError:
                    return  # campo vazio ou incompleto durante a digitação
                if 0 <= p <= 100 and c >= 0:
                    self._skills[i]["pct"], self._skills[i]["cooldown_s"] = p, c
                    self._save()
            pct.trace_add("write", save)
            cd.trace_add("write", save)

    # ---------------------------------------------------------------- teste
    def read_now(self) -> None:
        region = self.cfg.get("regiao_vida")
        if not region:
            self.test_lbl.config(text="Defina o 'Local Life Bar' na aba Captura primeiro.")
            return
        r = LifeBarReader(SpriteTemplate.from_file(LIFEBAR_PATH), float(self.cfg["vida_limiar"])).read(capture.grab(region))
        if r.pct is None:
            self.test_lbl.config(text=f"Barra de vida NÃO encontrada (similaridade {r.score:.2f}). Teste na aba Captura.")
            return
        from core.lifebar import pick_skill
        from core.revive import normalize_skills
        sk = pick_skill(normalize_skills(self.cfg.get("vida_hab_lista")), r.pct)
        self.test_lbl.config(text=f"Vida lida: {r.pct:.0f}%  →  " + (
            f"valeria a regra ≤{sk['pct']:g}% (tecla {sk['tecla'].upper()})." if sk else "nenhuma regra se aplica."))
