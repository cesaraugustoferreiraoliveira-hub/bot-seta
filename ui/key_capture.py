"""Captura a próxima tecla apertada pelo usuário (Esc cancela) sem travar a janela."""
from __future__ import annotations
import queue
import threading
import tkinter as tk


class KeyCapture:
    """Uma instância por página. capture() espera uma tecla em outra thread e entrega o nome a `on_key` no tkinter."""

    def __init__(self, widget: tk.Misc):
        self.widget = widget
        self._busy = False
        self._q: queue.SimpleQueue = queue.SimpleQueue()
        self._btn = self._on_key = self._on_done = self._on_error = None
        widget.after(150, self._poll)

    def capture(self, on_key, button=None, on_done=None, on_error=None) -> None:
        if self._busy:
            return
        self._busy = True
        self._btn, self._on_key, self._on_done, self._on_error = button, on_key, on_done, on_error
        if button is not None:
            button.config(state="disabled")

        def worker():
            try:
                import keyboard
                self._q.put(("key", keyboard.read_key(suppress=False)))
            except Exception as exc:  # noqa: BLE001
                self._q.put(("err", str(exc)))
        threading.Thread(target=worker, daemon=True).start()

    def _poll(self) -> None:
        try:
            while True:
                kind, val = self._q.get_nowait()
                self._busy = False
                btn, on_key, on_done, on_error = self._btn, self._on_key, self._on_done, self._on_error
                if btn is not None:
                    btn.config(state="normal")
                if kind == "key" and val and str(val).lower() != "esc" and on_key:
                    on_key(str(val).lower())
                elif kind == "err" and on_error:
                    on_error(f"Não consegui capturar a tecla: {val}")
                if on_done:
                    on_done()
        except queue.Empty:
            pass
        try:
            self.widget.after(150, self._poll)
        except tk.TclError:
            pass  # janela fechada
