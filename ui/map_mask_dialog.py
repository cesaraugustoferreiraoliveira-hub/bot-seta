"""Editor do mapa completo: o usuário marca o que é CORREDOR (verde) e o que é OBSTÁCULO (vermelho)."""
from __future__ import annotations
import tkinter as tk
from tkinter import messagebox, ttk

import cv2
import numpy as np
from PIL import Image, ImageTk

from core import mapping
from core.config import MAPMASK_PATH
from core.mapmask import CORRIDOR, OBSTACLE, MapMask

MAX_W, MAX_H = 760, 520


class MapMaskDialog(tk.Toplevel):
    """Uso:  dlg = MapMaskDialog(master, mapmask, tolerancia); master.wait_window(dlg); dlg.saved"""

    def __init__(self, master, mm: MapMask, tolerance: int = 12):
        super().__init__(master)
        self.title("Corredor e obstáculos do mapa completo")
        self.transient(master.winfo_toplevel())
        self.resizable(False, False)
        self.mm = mm
        self.saved = False
        h, w = mm.bgr.shape[:2]
        self.scale = min(MAX_W / w, MAX_H / h, 12.0)
        self.W, self.H = max(1, int(w * self.scale)), max(1, int(h * self.scale))
        self._photo = None
        self._pending = False

        ttk.Label(self, wraplength=max(self.W, 560), justify="left", text=(
            "Botão ESQUERDO = CORREDOR (verde)   •   Botão DIREITO = OBSTÁCULO (vermelho).\n"
            "Varinha: um clique marca todos os pixels dessa cor.  Pincel: arraste para pintar.  "
            "Ctrl+Z desfaz.  O que ficar sem marcar conta como obstáculo.")
        ).grid(row=0, column=0, sticky="w", padx=10, pady=(10, 4))

        self.canvas = tk.Canvas(self, width=self.W, height=self.H, highlightthickness=1, cursor="crosshair")
        self.canvas.grid(row=1, column=0, padx=10, pady=4)

        opts = ttk.Frame(self)
        opts.grid(row=2, column=0, sticky="w", padx=10, pady=4)
        self.tool = tk.StringVar(value="varinha")
        ttk.Radiobutton(opts, text="Varinha (por cor)", variable=self.tool, value="varinha").pack(side="left")
        ttk.Radiobutton(opts, text="Pincel", variable=self.tool, value="pincel").pack(side="left", padx=(8, 16))
        ttk.Label(opts, text="Tolerância de cor").pack(side="left")
        self.tol = tk.IntVar(value=int(tolerance))
        ttk.Spinbox(opts, from_=0, to=120, width=4, textvariable=self.tol).pack(side="left", padx=(4, 12))
        self.only_connected = tk.BooleanVar(value=False)
        ttk.Checkbutton(opts, text="Só a região ligada ao clique", variable=self.only_connected).pack(side="left")
        ttk.Label(opts, text="   Tamanho do pincel").pack(side="left")
        self.brush = tk.IntVar(value=3)
        ttk.Spinbox(opts, from_=1, to=60, width=4, textvariable=self.brush).pack(side="left", padx=4)

        self.status = ttk.Label(self, text="")
        self.status.grid(row=3, column=0, sticky="w", padx=10, pady=2)

        bar = ttk.Frame(self)
        bar.grid(row=4, column=0, sticky="e", padx=10, pady=(4, 10))
        ttk.Button(bar, text="Desfazer", command=self.undo).pack(side="left", padx=4)
        ttk.Button(bar, text="Limpar tudo", command=self.clear).pack(side="left", padx=4)
        ttk.Button(bar, text="Sem marcar → obstáculo", command=self.unset_to_obstacle).pack(side="left", padx=4)
        ttk.Button(bar, text="Cancelar", command=self.destroy).pack(side="left", padx=(16, 4))
        ttk.Button(bar, text="Salvar", command=self.save).pack(side="left", padx=4)

        for press, motion, value in (("<ButtonPress-1>", "<B1-Motion>", CORRIDOR),
                                     ("<ButtonPress-3>", "<B3-Motion>", OBSTACLE),
                                     ("<ButtonPress-2>", "<B2-Motion>", OBSTACLE)):  # 2 = botão direito no Mac
            self.canvas.bind(press, lambda e, v=value: self._press(e, v))
            self.canvas.bind(motion, lambda e, v=value: self._drag(e, v))
        self.bind("<Control-z>", lambda e: self.undo())
        self.bind("<Escape>", lambda e: self.destroy())

        self.render()
        self.update_idletasks()
        self.grab_set()
        self.focus_force()

    # ---------------------------------------------------------------- interação
    def _map_xy(self, e) -> tuple[int, int]:
        h, w = self.mm.state.shape
        return min(max(int(e.x / self.scale), 0), w - 1), min(max(int(e.y / self.scale), 0), h - 1)

    def _int(self, var: tk.IntVar, default: int) -> int:
        try:
            return int(var.get())
        except (tk.TclError, ValueError):
            return default  # campo vazio durante a digitação

    def _press(self, e, value: int) -> None:
        self.mm.push_undo()
        x, y = self._map_xy(e)
        if self.tool.get() == "varinha":
            self.mm.apply_color(x, y, value, self._int(self.tol, 12), not self.only_connected.get())
        else:
            self.mm.paint(x, y, self._int(self.brush, 3), value)
        self.schedule_render()

    def _drag(self, e, value: int) -> None:
        if self.tool.get() == "pincel":
            x, y = self._map_xy(e)
            self.mm.paint(x, y, self._int(self.brush, 3), value)
            self.schedule_render()

    def undo(self) -> None:
        if self.mm.undo():
            self.schedule_render()

    def clear(self) -> None:
        self.mm.push_undo()
        self.mm.clear()
        self.schedule_render()

    def unset_to_obstacle(self) -> None:
        self.mm.push_undo()
        self.mm.unset_to_obstacle()
        self.schedule_render()

    def save(self) -> None:
        walk = self.mm.walkable()
        if not walk.any():
            messagebox.showwarning("Salvar", "Marque pelo menos uma área como CORREDOR (clique esquerdo).", parent=self)
            return
        try:
            mapping.build_loop(walk, "horario")
        except (ValueError, ZeroDivisionError) as exc:
            messagebox.showwarning("Salvar", f"Não deu para montar a rota com essas marcações:\n{exc}", parent=self)
            return
        self.mm.save_mask(MAPMASK_PATH)
        self.saved = True
        self.destroy()

    # ---------------------------------------------------------------- desenho
    def schedule_render(self) -> None:
        if not self._pending:
            self._pending = True
            self.after_idle(self._do_render)

    def _do_render(self) -> None:
        self._pending = False
        if self.winfo_exists():
            self.render()

    def render(self) -> None:
        view = cv2.resize(self.mm.bgr, (self.W, self.H), interpolation=cv2.INTER_NEAREST).astype(np.float32)
        st = cv2.resize(self.mm.state, (self.W, self.H), interpolation=cv2.INTER_NEAREST)
        for value, color in ((CORRIDOR, (0, 200, 0)), (OBSTACLE, (0, 0, 230))):  # BGR
            m = st == value
            view[m] = view[m] * 0.55 + np.array(color, np.float32) * 0.45
        rgb = cv2.cvtColor(view.astype(np.uint8), cv2.COLOR_BGR2RGB)
        self._photo = ImageTk.PhotoImage(Image.fromarray(rgb))
        self.canvas.delete("all")
        self.canvas.create_image(0, 0, image=self._photo, anchor="nw")
        s = self.mm.stats()
        self.status.config(text=f"Corredor {s['corredor']:.0%}   •   Obstáculo {s['obstaculo']:.0%}   •   "
                                f"Sem marcar {s['sem_marcar']:.0%} (conta como obstáculo)")
