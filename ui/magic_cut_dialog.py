"""Diálogo do recorte mágico: o usuário marca o que é a sprite, o algoritmo isola, o usuário confirma."""
from __future__ import annotations
import tkinter as tk
from tkinter import ttk

import cv2
import numpy as np
from PIL import Image, ImageTk

from core.magic_cut import crop_bgra, isolate_sprite
from .imgutil import over_checkerboard

MAX_W, MAX_H = 520, 420


class MagicCutDialog(tk.Toplevel):
    """Uso:  dlg = MagicCutDialog(master, crop_pil); master.wait_window(dlg); dlg.result  (BGRA ou None)"""

    def __init__(self, master, crop: Image.Image):
        super().__init__(master)
        self.title("Recorte mágico da sprite")
        self.transient(master.winfo_toplevel())
        self.resizable(False, False)
        self.result: np.ndarray | None = None

        self.bgr = cv2.cvtColor(np.array(crop.convert("RGB")), cv2.COLOR_RGB2BGR)
        h, w = self.bgr.shape[:2]
        s = min(MAX_W / w, MAX_H / h, 12.0)
        self.scale = float(int(s)) if s >= 1 else s
        self.W, self.H = max(1, int(w * self.scale)), max(1, int(h * self.scale))
        self.points: list[tuple[float, float, bool]] = []   # (x, y, é_sprite) na ordem dos cliques
        self.mask: np.ndarray | None = None
        self._photos: dict[str, ImageTk.PhotoImage] = {}

        ttk.Label(self, wraplength=self.W * 2 + 60, justify="left", text=(
            "1) Clique com o botão ESQUERDO em cima da sprite (várias partes, se ela tiver várias cores).\n"
            "2) Se o recorte pegar fundo demais, clique com o botão DIREITO no que NÃO é sprite.\n"
            "3) Confira o resultado à direita e confirme.  (Ctrl+Z desfaz o último clique)")
        ).grid(row=0, column=0, columnspan=2, sticky="w", padx=10, pady=(10, 4))

        lf = ttk.LabelFrame(self, text=" Sua seleção ")
        lf.grid(row=1, column=0, padx=10, pady=4, sticky="n")
        self.canvas = tk.Canvas(lf, width=self.W, height=self.H, highlightthickness=0, cursor="crosshair")
        self.canvas.pack(padx=4, pady=4)

        rf = ttk.LabelFrame(self, text=" Sprite isolada ")
        rf.grid(row=1, column=1, padx=10, pady=4, sticky="n")
        self.out = tk.Canvas(rf, width=min(MAX_W, 300), height=min(MAX_H, 300), highlightthickness=0, bg="#f0f0f0")
        self.out.pack(padx=4, pady=4)

        self.status = ttk.Label(self, text="Clique em cima da sprite para começar.")
        self.status.grid(row=2, column=0, columnspan=2, sticky="w", padx=10, pady=4)

        bar = ttk.Frame(self)
        bar.grid(row=3, column=0, columnspan=2, sticky="e", padx=10, pady=(4, 10))
        ttk.Button(bar, text="Limpar marcações", command=self.clear).pack(side="left", padx=4)
        ttk.Button(bar, text="Cancelar", command=self.destroy).pack(side="left", padx=4)
        self.ok_btn = ttk.Button(bar, text="Confirmar", command=self.confirm, state="disabled")
        self.ok_btn.pack(side="left", padx=4)

        self.canvas.bind("<Button-1>", lambda e: self.add_point(e, True))
        for seq in ("<Button-3>", "<Button-2>"):   # botão direito (Windows/Linux) e Mac
            self.canvas.bind(seq, lambda e: self.add_point(e, False))
        self.bind("<Control-z>", lambda e: self.undo())
        self.bind("<Return>", lambda e: self.confirm())
        self.bind("<Escape>", lambda e: self.destroy())

        self.render()
        self.update_idletasks()
        self.grab_set()
        self.focus_force()

    # ---------------------------------------------------------------- interação
    @property
    def fg(self):
        return [(x, y) for x, y, is_sprite in self.points if is_sprite]

    @property
    def bg(self):
        return [(x, y) for x, y, is_sprite in self.points if not is_sprite]

    def add_point(self, event, is_sprite: bool) -> None:
        self.points.append((event.x / self.scale, event.y / self.scale, is_sprite))
        self.recompute()

    def undo(self) -> None:
        if self.points:
            self.points.pop()
            self.recompute()

    def clear(self) -> None:
        self.points.clear()
        self.recompute()

    def confirm(self) -> None:
        if self.mask is not None:
            self.result = crop_bgra(self.bgr, self.mask)
            self.destroy()

    # ---------------------------------------------------------------- algoritmo + desenho
    def recompute(self) -> None:
        self.mask = isolate_sprite(self.bgr, self.fg, self.bg)
        self.render()

    def render(self) -> None:
        view = cv2.resize(self.bgr, (self.W, self.H), interpolation=cv2.INTER_NEAREST)
        if self.mask is not None:
            big = cv2.resize(self.mask.astype(np.uint8), (self.W, self.H), interpolation=cv2.INTER_NEAREST)
            view[big == 0] = (view[big == 0] * 0.35).astype(np.uint8)
            cnts, _ = cv2.findContours(big, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
            cv2.drawContours(view, cnts, -1, (0, 255, 255), 1)
        self._photos["left"] = ImageTk.PhotoImage(Image.fromarray(cv2.cvtColor(view, cv2.COLOR_BGR2RGB)))
        self.canvas.delete("all")
        self.canvas.create_image(0, 0, image=self._photos["left"], anchor="nw")
        for x, y, is_sprite in self.points:
            color = "#00e000" if is_sprite else "#ff2020"
            cx, cy = x * self.scale, y * self.scale
            self.canvas.create_oval(cx - 4, cy - 4, cx + 4, cy + 4, outline="white", width=3)
            self.canvas.create_oval(cx - 4, cy - 4, cx + 4, cy + 4, outline=color, width=2)

        self.out.delete("all")
        if self.mask is None:
            self.ok_btn.state(["disabled"])
            self.status.config(text="Clique em cima da sprite para começar." if not self.points
                               else "Não consegui isolar nada: clique em mais partes da sprite.")
            return
        bgra = crop_bgra(self.bgr, self.mask)
        rgba = cv2.cvtColor(bgra, cv2.COLOR_BGRA2RGBA)
        oh, ow = rgba.shape[:2]
        s2 = min(300 / ow, 300 / oh, 12.0)
        s2 = float(int(s2)) if s2 >= 1 else s2
        big = cv2.resize(rgba, (max(1, int(ow * s2)), max(1, int(oh * s2))), interpolation=cv2.INTER_NEAREST)
        self._photos["right"] = ImageTk.PhotoImage(over_checkerboard(big))
        self.out.create_image(150, 150, image=self._photos["right"])
        self.ok_btn.state(["!disabled"])
        self.status.config(text=f"Sprite isolada: {ow}×{oh} px — está certa? Se sim, confirme.")
