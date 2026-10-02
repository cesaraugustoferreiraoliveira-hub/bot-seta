"""Seleção de uma área da tela arrastando o mouse (tela congelada em tela cheia)."""
from __future__ import annotations
import time
import tkinter as tk

import pyautogui
from PIL import Image, ImageEnhance, ImageTk


def select_region(root: tk.Misc, instrucao: str = "Arraste para selecionar a área  •  Esc cancela"):
    """Retorna ([x, y, w, h], imagem_da_tela_inteira) ou (None, None) se cancelar."""
    win = root.winfo_toplevel()
    win.withdraw()
    win.update()
    time.sleep(0.35)
    shot = pyautogui.screenshot()

    top = tk.Toplevel(win)
    top.attributes("-fullscreen", True)
    top.attributes("-topmost", True)
    top.configure(cursor="crosshair")
    top.update()
    sw, sh = top.winfo_screenwidth(), top.winfo_screenheight()
    fx, fy = shot.width / sw, shot.height / sh

    dim = ImageEnhance.Brightness(shot).enhance(0.55).resize((sw, sh))
    photo = ImageTk.PhotoImage(dim)
    canvas = tk.Canvas(top, width=sw, height=sh, highlightthickness=0)
    canvas.pack(fill="both", expand=True)
    canvas.create_image(0, 0, image=photo, anchor="nw")
    canvas.create_text(sw // 2 + 1, 25, fill="black", font=("Segoe UI", 14, "bold"), text=instrucao)
    canvas.create_text(sw // 2, 24, fill="white", font=("Segoe UI", 14, "bold"), text=instrucao)

    state = {"start": None, "rect": None, "result": None}

    def press(e):
        state["start"] = (e.x, e.y)
        state["rect"] = canvas.create_rectangle(e.x, e.y, e.x, e.y, outline="#ff3030", width=2)

    def drag(e):
        if state["rect"]:
            canvas.coords(state["rect"], *state["start"], e.x, e.y)

    def release(e):
        if not state["start"]:
            return
        x0, y0 = state["start"]
        x, y, w, h = min(x0, e.x), min(y0, e.y), abs(e.x - x0), abs(e.y - y0)
        if w >= 3 and h >= 3:
            state["result"] = [int(x * fx), int(y * fy), max(1, int(w * fx)), max(1, int(h * fy))]
        top.destroy()

    canvas.bind("<ButtonPress-1>", press)
    canvas.bind("<B1-Motion>", drag)
    canvas.bind("<ButtonRelease-1>", release)
    top.bind("<Escape>", lambda e: top.destroy())
    top.focus_force()
    top.wait_window()

    win.deiconify()
    return (state["result"], shot) if state["result"] else (None, None)


def crop_region(shot: Image.Image, region) -> Image.Image:
    x, y, w, h = region
    return shot.crop((x, y, x + w, y + h))
