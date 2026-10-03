"""Módulo da GUI: bolas da aba pokeball. Cada linha é um perfil: a sprite do pokémon morto (recorte mágico), a tecla da
bola, a prioridade (1 = joga primeiro) e a similaridade mínima. Por padrão há dois: shiny (Premier Ball) e normal
(Ultra Ball); o usuário pode ajustar tudo e acrescentar outros perfis. A lógica fica em core/pokeball.py."""
from __future__ import annotations
import tkinter as tk
from tkinter import messagebox, ttk

import cv2
import numpy as np

from core.config import ConfigStore, POKEBALL_DIR
from core.magic_cut import load_template, save_template
from core.pokeball import FIXED_IDS, parse_profiles, sprite_path

MODE_LABELS = {"toque": "Toque", "segurar": "Segurar"}
from .fields import key_label
from .imgutil import over_checkerboard, to_photo
from .key_capture import KeyCapture
from .magic_cut_dialog import MagicCutDialog
from .region_selector import crop_region, select_region


class BallProfilesPanel(ttk.LabelFrame):
    def __init__(self, master, cfg: ConfigStore, keys: KeyCapture):
        super().__init__(master, text=" Bolas e sprites dos pokémon mortos ")
        self.cfg, self.keys = cfg, keys
        self._photos: list = []
        self.profiles = parse_profiles(cfg.get("pokeball_perfis"))
        self._save()                                  # grava os dois perfis padrão se a config ainda não tinha
        ttk.Label(self, foreground="#666", justify="left", wraplength=1000, text=(
            "Cada linha é um tipo de pokémon morto: a sprite, a tecla da bola e a prioridade (1 joga primeiro: o shiny sai "
            "antes de qualquer outra).\nToque = o mouse vai à sprite e a tecla é tocada. Segurar = a tecla fica SEGURADA pelo "
            "tempo definido enquanto o mouse percorre as sprites, e é solta assim que não sobra nenhuma (mais garantido).")
                  ).grid(row=0, column=0, sticky="w", padx=10, pady=(6, 2))
        self.rows = ttk.Frame(self)
        self.rows.grid(row=1, column=0, sticky="ew", padx=6, pady=2)
        ttk.Button(self, text="+ Adicionar bola", command=self._add).grid(row=2, column=0, sticky="w", padx=10, pady=(2, 8))
        self._render()

    # ---------------------------------------------------------------- persistência
    def _save(self) -> None:
        self.cfg.set("pokeball_perfis", [dict(p) for p in self.profiles])

    def _find(self, pid: str) -> dict:
        return next(p for p in self.profiles if p["id"] == pid)

    # ---------------------------------------------------------------- linhas
    def _render(self) -> None:
        for w in self.rows.winfo_children():
            w.destroy()
        self._photos = []
        for col, text in enumerate(("Sprite", "Nome", "Tecla da bola", "", "Prior.", "Similar.", "Modo", "Segurar por", "", "")):
            if text:
                ttk.Label(self.rows, text=text, foreground="#555").grid(row=0, column=col, padx=4, sticky="w")
        for i, p in enumerate(sorted(self.profiles, key=lambda q: (q["prioridade"], q["id"])), start=1):
            self._row(i, p["id"])

    def _row(self, r: int, pid: str) -> None:
        p = self._find(pid)
        box = ttk.Label(self.rows, text="(sem sprite)", anchor="center", relief="sunken", width=12)
        box.grid(row=r, column=0, padx=4, pady=3, ipady=10)
        self._show_sprite(box, pid)

        name = tk.StringVar(value=p["nome"])
        ttk.Entry(self.rows, textvariable=name, width=20).grid(row=r, column=1, padx=4)
        name.trace_add("write", lambda *a, pid=pid, v=name: self._set(pid, "nome", v.get().strip() or pid))

        key_label(self.rows, p["tecla"], width=6).grid(row=r, column=2, padx=(4, 0))
        btn = ttk.Button(self.rows, text="Alterar", width=7)
        btn.grid(row=r, column=3, padx=4)
        btn.config(command=lambda pid=pid, btn=btn: self._change_key(pid, btn))

        prio = tk.StringVar(value=str(p["prioridade"]))
        ttk.Spinbox(self.rows, from_=1, to=99, width=4, textvariable=prio).grid(row=r, column=4, padx=4)
        prio.trace_add("write", lambda *a, pid=pid, v=prio: self._set_num(pid, "prioridade", v, 1, 99, int))

        lim = tk.StringVar(value=f"{p['limiar']:.2f}")
        ttk.Spinbox(self.rows, from_=0.3, to=1.0, increment=0.01, format="%.2f", width=5,
                    textvariable=lim).grid(row=r, column=5, padx=4)
        lim.trace_add("write", lambda *a, pid=pid, v=lim: self._set_num(pid, "limiar", v, 0.3, 1.0, float))

        modo = ttk.Combobox(self.rows, values=list(MODE_LABELS.values()), state="readonly", width=8)
        modo.set(MODE_LABELS.get(p.get("modo", "toque"), "Toque"))
        modo.grid(row=r, column=6, padx=4)
        hold_box = ttk.Frame(self.rows)
        hold_box.grid(row=r, column=7, padx=4, sticky="w")
        hold = tk.StringVar(value=f"{float(p.get('segurar_s', 2.0)):g}")
        spin = ttk.Spinbox(hold_box, from_=0.1, to=60, increment=0.5, width=5, textvariable=hold)
        spin.pack(side="left")
        ttk.Label(hold_box, text="s").pack(side="left", padx=(3, 0))
        hold.trace_add("write", lambda *a, pid=pid, v=hold: self._set_num(pid, "segurar_s", v, 0.1, 60, float))

        def on_mode(_e=None, pid=pid, cb=modo, spin=spin):
            self._set(pid, "modo", next(k for k, v in MODE_LABELS.items() if v == cb.get()))
            spin.config(state="normal" if cb.get() == MODE_LABELS["segurar"] else "disabled")
        modo.bind("<<ComboboxSelected>>", on_mode)
        spin.config(state="normal" if p.get("modo") == "segurar" else "disabled")

        on = tk.BooleanVar(value=p["ativo"])
        ttk.Checkbutton(self.rows, text="ativa", variable=on,
                        command=lambda pid=pid, v=on: self._set(pid, "ativo", bool(v.get()))).grid(row=r, column=8, padx=4)
        actions = ttk.Frame(self.rows)
        actions.grid(row=r, column=9, padx=4, sticky="w")
        ttk.Button(actions, text="Selecionar sprite", command=lambda pid=pid, b=box: self._pick_sprite(pid, b)).pack(side="top", fill="x")
        if pid not in FIXED_IDS:
            ttk.Button(actions, text="Remover", command=lambda pid=pid: self._remove(pid)).pack(side="top", fill="x", pady=(2, 0))

    def _show_sprite(self, label: ttk.Label, pid: str) -> None:
        loaded = load_template(sprite_path(pid))
        if loaded is None:
            return
        bgr, mask = loaded
        rgba = cv2.cvtColor(np.dstack([bgr, mask]), cv2.COLOR_BGRA2RGBA)
        photo = to_photo(over_checkerboard(rgba), 80, 60)
        self._photos.append(photo)                    # evita o garbage collector
        label.config(image=photo, text="", width=0)

    # ---------------------------------------------------------------- edição
    def _set(self, pid: str, key: str, value) -> None:
        self._find(pid)[key] = value
        self._save()

    def _set_num(self, pid: str, key: str, var: tk.StringVar, lo, hi, cast) -> None:
        try:
            v = cast(float(var.get().replace(",", ".")))
        except ValueError:
            return                                    # campo vazio durante a digitação
        if lo <= v <= hi:
            self._set(pid, key, v)

    def _change_key(self, pid: str, btn: ttk.Button) -> None:
        def got(k: str) -> None:
            self._set(pid, "tecla", k)

        self.keys.capture(got, btn, on_done=self._render)
        btn.config(text="aperte…")

    def _pick_sprite(self, pid: str, label: ttk.Label) -> None:
        region, shot = select_region(
            self, "Desenhe uma caixa que CONTENHA a sprite do pokémon morto (com um pouco de fundo em volta)  •  Esc cancela")
        if not region:
            return
        dlg = MagicCutDialog(self, crop_region(shot, region))
        self.wait_window(dlg)
        if dlg.result is not None:
            POKEBALL_DIR.mkdir(exist_ok=True)
            save_template(dlg.result, sprite_path(pid))
            self._render()

    def _add(self) -> None:
        n = 1
        while any(p["id"] == f"extra{n}" for p in self.profiles):
            n += 1
        nxt = max((p["prioridade"] for p in self.profiles), default=0) + 1
        self.profiles.append({"id": f"extra{n}", "nome": f"Nova bola {n}", "tecla": "", "prioridade": nxt,
                              "limiar": 0.90, "ativo": True,
                              "modo": "toque", "segurar_s": 2.0})
        self._save()
        self._render()

    def _remove(self, pid: str) -> None:
        p = self._find(pid)
        if not messagebox.askyesno("Remover bola", f"Remover '{p['nome']}' e a sprite dela?", parent=self):
            return
        self.profiles = [q for q in self.profiles if q["id"] != pid]
        try:
            sprite_path(pid).unlink()
        except OSError:
            pass
        self._save()
        self._render()
