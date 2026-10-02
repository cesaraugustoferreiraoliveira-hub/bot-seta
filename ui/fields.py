"""Campos reutilizáveis das páginas: linha com número (salva na config ao digitar) e linha com tecla."""
from __future__ import annotations
import tkinter as tk
from tkinter import ttk

from core.config import ConfigStore


def number_row(parent, row: int, cfg: ConfigStore, label: str, key: str, lo: float, hi: float, step: float,
               unit: str, hint: str, cast=float) -> None:
    ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", padx=10, pady=3)
    box = ttk.Frame(parent)
    box.grid(row=row, column=1, sticky="w", pady=3)
    var = tk.StringVar(value=f"{cfg.get(key):g}")
    ttk.Spinbox(box, from_=lo, to=hi, increment=step, width=7, textvariable=var).pack(side="left")
    ttk.Label(box, text=unit).pack(side="left", padx=(4, 0))
    ttk.Label(parent, text=hint, foreground="#666").grid(row=row, column=2, sticky="w", padx=8)

    def save(*_):
        try:
            v = cast(float(var.get().replace(",", ".")))
        except ValueError:
            return  # campo vazio ou incompleto durante a digitação
        if lo <= v <= hi:
            cfg.set(key, v)
    var.trace_add("write", save)


def key_label(parent, text: str, width: int = 8) -> ttk.Label:
    return ttk.Label(parent, text=(text or "—").upper(), width=width, relief="groove", anchor="center",
                     font=("Segoe UI", 10, "bold"))
