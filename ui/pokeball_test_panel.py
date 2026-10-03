"""Módulo da GUI: testes da aba pokeball — ver o que o bot enxerga (e a ordem das bolas) e um arremesso de teste de verdade."""
from __future__ import annotations
import time
import tkinter as tk
from tkinter import ttk

import cv2
from PIL import Image

from core import capture
from core.botlog import BotLog
from core.config import ConfigStore
from core.pokeball import PokeballThrower, detect, load_balls, order_targets
from .imgutil import to_photo

COLORS = [(255, 0, 255), (0, 200, 0), (0, 165, 255), (255, 160, 0), (0, 0, 255), (0, 220, 255)]   # BGR, por ordem de prioridade
COUNTDOWN_S = 5


def search_region(cfg: ConfigStore):
    r = cfg.get("regiao_pokeball") or cfg.get("regiao_sprite")
    return [int(v) for v in r] if r else None


class PokeballTestPanel(ttk.LabelFrame):
    def __init__(self, master, cfg: ConfigStore, log: BotLog | None = None):
        super().__init__(master, text=" Testes ")
        self.cfg, self.log = cfg, log
        self._preview_win = None
        self._thrower: PokeballThrower | None = None

        row = ttk.Frame(self)
        row.grid(row=0, column=0, sticky="w", padx=10, pady=(8, 2))
        ttk.Button(row, text="Testar detecção agora", command=self.test_detect).pack(side="left")
        ttk.Label(row, text="mostra as sprites achadas, a ordem das bolas e o tempo da varredura",
                  foreground="#666").pack(side="left", padx=8)

        row2 = ttk.Frame(self)
        row2.grid(row=1, column=0, sticky="w", padx=10, pady=2)
        self.throw_btn = ttk.Button(row2, text=f"Teste de arremesso ({COUNTDOWN_S} s depois de clicar)", command=self.test_throw)
        self.throw_btn.pack(side="left")
        ttk.Label(row2, text="vá ao jogo, mate os pokémon e veja as bolas (usa mouse e teclas de verdade)",
                  foreground="#666").pack(side="left", padx=8)

        self.lbl = ttk.Label(self, text="", wraplength=720, justify="left")
        self.lbl.grid(row=2, column=0, sticky="w", padx=10, pady=(4, 8))

    # ---------------------------------------------------------------- ver o que o bot enxerga
    def test_detect(self) -> None:
        region = search_region(self.cfg)
        if region is None:
            self.lbl.config(text="Defina a 'Área de busca da sprite' (aba sprites/capture) ou a área da pokeball (aba Ajustes).")
            return
        balls, problems = load_balls(self.cfg)
        if not balls:
            self.lbl.config(text="Nenhuma bola pronta: " + ("; ".join(problems) if problems else "ative um perfil."))
            return
        t0 = time.perf_counter()
        frame = capture.grab(region)
        if any(frame.shape[0] < b.tmpl.h or frame.shape[1] < b.tmpl.w for b in balls):
            self.lbl.config(text="A sprite é maior que a área de busca.")
            return
        dets = order_targets(detect(frame, balls))
        ms = (time.perf_counter() - t0) * 1000
        per = {}
        for d in dets:
            per[d.ball.nome] = per.get(d.ball.nome, 0) + 1
        resumo = ", ".join(f"{n}: {c}" for n, c in per.items()) or "nenhuma sprite"
        txt = f"{len(dets)} sprite(s) na área ({resumo}). Uma varredura completa levou {ms:.0f} ms."
        if problems:
            txt += "  Ignorados: " + "; ".join(problems) + "."
        if ms > 600:
            txt += "  Está lenta: use uma área menor, só ao redor do personagem (aba Ajustes)."
        self.lbl.config(text=txt)
        self._show_preview(frame, dets, balls)

    def _show_preview(self, frame, dets, balls) -> None:
        img = frame.copy()
        h, w = img.shape[:2]
        s = min(1100 / w, 700 / h, 1.0)
        th, fs = max(1, round(1 / s)), 0.5 / s
        rank = {b.id: i for i, b in enumerate(sorted(balls, key=lambda b: b.prioridade))}
        for n, d in enumerate(dets, start=1):
            col = COLORS[rank.get(d.ball.id, 0) % len(COLORS)]
            x0, y0 = int(round(d.x - d.ball.tmpl.w / 2)) - 3, int(round(d.y - d.ball.tmpl.h / 2)) - 3
            cv2.rectangle(img, (x0, y0), (x0 + d.ball.tmpl.w + 6, y0 + d.ball.tmpl.h + 6), col, th)
            label = f"{n}  {d.ball.tecla.upper()}  {d.score:.2f}"
            pos = (x0, max(14, y0 - 4))
            cv2.putText(img, label, pos, cv2.FONT_HERSHEY_SIMPLEX, fs, (0, 0, 0), th + 2, cv2.LINE_AA)
            cv2.putText(img, label, pos, cv2.FONT_HERSHEY_SIMPLEX, fs, col, th, cv2.LINE_AA)
        if self._preview_win is not None and self._preview_win.winfo_exists():
            self._preview_win.destroy()
        win = tk.Toplevel(self)
        win.title("Ordem das bolas: nº, tecla, similaridade (a cor segue a prioridade do perfil)")
        self._preview_win = win
        photo = to_photo(Image.fromarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB)), 1100, 700, max_scale=1)
        lbl = ttk.Label(win, image=photo)
        lbl.image = photo                              # evita o garbage collector
        lbl.pack(padx=8, pady=8)

    # ---------------------------------------------------------------- arremesso de teste
    def test_throw(self) -> None:
        if self._thrower is not None:
            return
        balls, problems = load_balls(self.cfg)
        if not balls:
            self.lbl.config(text="Nenhuma bola pronta: " + ("; ".join(problems) if problems else "ative um perfil."))
            return
        if search_region(self.cfg) is None:
            self.lbl.config(text="Defina a 'Área de busca da sprite' (aba sprites/capture) ou a área da pokeball (aba Ajustes).")
            return
        self.throw_btn.config(state="disabled")
        self._countdown(COUNTDOWN_S)

    def _countdown(self, left: int) -> None:
        if left > 0:
            self.lbl.config(text=f"Teste de arremesso começa em {left} s: vá para o jogo e mate os pokémon…")
            self.after(1000, lambda: self._countdown(left - 1))
            return
        window = float(self.cfg.get("pokeball_janela_s", 8.0) or 8.0)
        self._thrower = PokeballThrower(self.cfg, self.log.add if self.log is not None else None)
        self._thrower.start()
        self._thrower.test_window(window)
        self.lbl.config(text=f"Procurando e jogando bolas por {window:.0f} s…")
        self.after(int((window + 1.0) * 1000), self._finish_throw)

    def _finish_throw(self) -> None:
        th, self._thrower = self._thrower, None
        if th is None:
            return
        th.stop()
        total = sum(th.stats.values())
        per = ", ".join(f"{n}: {c}" for n, c in th.stats.items()) or "nenhuma"
        self.lbl.config(text=f"Teste terminado: {total} bola(s) jogada(s) ({per}). Última varredura: {th.last_scan_s * 1000:.0f} ms. "
                             "Os detalhes de cada bola estão no log da aba engine.")
        self.throw_btn.config(state="normal")
