"""Módulo da GUI: Shooter — com o bot parado por excesso de sprites, atira (mouse + tecla) na sprite distante
que parou de andar. Aqui o usuário define a distância limite, o tempo de parada e a tecla; a lógica fica em core/shooter.py.
"""
from __future__ import annotations
import math
import queue
import threading
import tkinter as tk
from tkinter import ttk

import cv2
from PIL import Image

from core import capture, vision
from core.config import ConfigStore, POKEMON_PATH, SPRITE_PATH
from core.name_match import NameTemplate
from core.shooter import locate_pokemon, parse_sequence
from .imgutil import to_photo

INSIDE, OUTSIDE, REF = (0, 200, 0), (0, 0, 255), (255, 0, 255)   # BGR: dentro do limite / além / centro do círculo
NAME, AIM = (0, 220, 255), (255, 160, 0)                          # BGR: nome do pokémon (amarelo) / ponto do clique (azul)


class ShooterPanel(ttk.LabelFrame):
    def __init__(self, master, cfg: ConfigStore):
        super().__init__(master, text=" Shooter: atirar na sprite distante que parou ")
        self.cfg = cfg
        self._preview_win = None
        self._capturing = False
        self._on_key = self._on_done = self._btn = None     # quem recebe a tecla capturada
        self._q: queue.SimpleQueue = queue.SimpleQueue()   # a captura da tecla roda em outra thread
        self.columnconfigure(2, weight=1)

        self.ativo = tk.BooleanVar(value=bool(cfg.get("shooter_ativo")))
        ttk.Checkbutton(self, text="Ativar o shooter (só age enquanto o bot está parado por excesso de sprites)",
                        variable=self.ativo, command=lambda: cfg.set("shooter_ativo", bool(self.ativo.get()))
                        ).grid(row=0, column=0, columnspan=3, sticky="w", padx=10, pady=(8, 4))

        self._num(1, "Distância limite", "shooter_distancia_px", 10, 3000, 10, "px",
                  "entre o seu pokémon e a sprite; só as sprites MAIS LONGE que isso interessam", int)
        self._num(2, "Sprite parada por", "shooter_parada_s", 0.3, 60, 0.1, "s",
                  "tempo, em várias capturas seguidas, sem a sprite distante se mexer", float)
        self._num(3, "Oscilação tolerada", "shooter_tolerancia_px", 1, 100, 1, "px",
                  "quanto a sprite pode variar de posição e ainda contar como parada", int)

        ttk.Label(self, text="Tecla do tiro").grid(row=4, column=0, sticky="w", padx=10, pady=3)
        krow = ttk.Frame(self)
        krow.grid(row=4, column=1, sticky="w", pady=3)
        self.key_lbl = ttk.Label(krow, text="", font=("Segoe UI", 10, "bold"), width=8, relief="groove", anchor="center")
        self.key_lbl.pack(side="left")
        self.key_btn = ttk.Button(krow, text="Alterar tecla", command=self._change_shot_key)
        self.key_btn.pack(side="left", padx=6)
        ttk.Label(self, text="o mouse vai até a sprite (mais o ajuste do clique) e a tecla é apertada ali", foreground="#666").grid(
            row=4, column=2, sticky="w", padx=8)
        self._show_key()

        self._num(5, "Espera máxima após o tiro", "shooter_espera_max_s", 0, 600, 1, "s",
                  "aguarda as sprites entrarem na distância limite; 0 = espera sem limite", float)
        self._num(6, "Ajuste do centro (para baixo)", "shooter_ref_dy_px", -500, 500, 1, "px",
                  "o nome fica ACIMA do pokémon: desce o centro do círculo até o corpo (veja no teste)", int)
        self._num(7, "Ajuste do clique (para baixo)", "shooter_mira_dy_px", -500, 500, 1, "px",
                  "a sprite é o emblema do selvagem: a tecla de tiro é apertada com o mouse este tanto ABAIXO dela", int)

        # ---- sequência de teclas quando todas as sprites estão dentro da distância limite
        seq = ttk.LabelFrame(self, text=" Quando TODAS as sprites reconhecidas estiverem dentro da distância limite ")
        seq.grid(row=8, column=0, columnspan=3, sticky="ew", padx=10, pady=(8, 2))
        self.seq_ativo = tk.BooleanVar(value=bool(cfg.get("shooter_seq_ativo")))
        ttk.Checkbutton(seq, text="Apertar esta sequência de teclas (a espera é o tempo DEPOIS de cada tecla, antes da próxima)",
                        variable=self.seq_ativo, command=lambda: cfg.set("shooter_seq_ativo", bool(self.seq_ativo.get()))
                        ).grid(row=0, column=0, sticky="w", padx=8, pady=(6, 2))
        self.seq_rows = ttk.Frame(seq)
        self.seq_rows.grid(row=1, column=0, sticky="w", padx=8)
        ttk.Button(seq, text="+ Adicionar tecla", command=self._seq_add).grid(row=2, column=0, sticky="w", padx=8, pady=(2, 8))
        self._seq = [dict(t) for t in (cfg.get("shooter_seq_teclas") or [])]
        self._seq_render()

        ttk.Button(self, text="Testar distâncias agora", command=self.test_distances).grid(
            row=9, column=0, columnspan=2, sticky="w", padx=10, pady=(6, 2))
        self.test_lbl = ttk.Label(self, text="", wraplength=640, justify="left")
        self.test_lbl.grid(row=10, column=0, columnspan=3, sticky="w", padx=10, pady=(2, 8))
        self._num(11, "Última verificação (intervalo)", "shooter_confirma_s", 0, 60, 0.1, "s",
                  "com todas dentro do limite, espera isto e confere de novo; se ainda não há sprite fora, dá o R", float)
        self._num(12, "Repetir o R durante", "shooter_seq_repetir_s", 0.1, 60, 0.5, "s",
                  "o R é apertado várias vezes neste tempo (1,5 s ≈ 10 apertos; mais tempo = mais apertos); depois vem o E", float)
        self._num(13, "Intervalo entre os R", "shooter_seq_intervalo_ms", 30, 1000, 10, "ms",
                  "tempo entre uma apertada do R e a próxima", int)
        self.after(100, self._poll)

    # ---------------------------------------------------------------- campos numéricos
    def _num(self, row, label, key, lo, hi, step, unit, hint, cast) -> None:
        ttk.Label(self, text=label).grid(row=row, column=0, sticky="w", padx=10, pady=3)
        box = ttk.Frame(self)
        box.grid(row=row, column=1, sticky="w", pady=3)
        var = tk.StringVar(value=f"{self.cfg.get(key):g}")
        ttk.Spinbox(box, from_=lo, to=hi, increment=step, width=7, textvariable=var).pack(side="left")
        ttk.Label(box, text=unit).pack(side="left", padx=(4, 0))
        ttk.Label(self, text=hint, foreground="#666").grid(row=row, column=2, sticky="w", padx=8)

        def save(*_):
            try:
                v = cast(float(var.get().replace(",", ".")))
            except ValueError:
                return  # campo vazio ou incompleto durante a digitação
            if lo <= v <= hi:
                self.cfg.set(key, v)
        var.trace_add("write", save)

    # ---------------------------------------------------------------- tecla do tiro
    def _show_key(self) -> None:
        self.key_lbl.config(text=str(self.cfg.get("shooter_tecla") or "—").upper())

    def _change_shot_key(self) -> None:
        def got(k):
            self.cfg.set("shooter_tecla", k)
            self._show_key()
        self.key_lbl.config(text="aperte…")
        self.capture_key(got, self.key_btn, self._show_key)

    def capture_key(self, on_key, button=None, on_done=None) -> None:
        """Espera o usuário apertar uma tecla (Esc cancela) e entrega o nome a `on_key`."""
        if self._capturing:
            return
        self._capturing = True
        self._on_key, self._on_done, self._btn = on_key, on_done, button
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
                self._capturing = False
                btn, on_key, on_done = self._btn, self._on_key, self._on_done
                if btn is not None:
                    btn.config(state="normal")                  # antes de a lista ser redesenhada (isso destrói o botão)
                if kind == "key" and val and str(val).lower() != "esc" and on_key:
                    on_key(str(val).lower())
                elif kind == "err":
                    self.test_lbl.config(text=f"Não consegui capturar a tecla: {val}")
                if on_done:
                    on_done()
        except queue.Empty:
            pass
        self.after(150, self._poll)

    # ---------------------------------------------------------------- sequência de teclas
    def _seq_save(self) -> None:
        self.cfg.set("shooter_seq_teclas", [dict(t) for t in self._seq])

    def _seq_add(self) -> None:
        self._seq.append({"tecla": "", "espera_ms": 300})
        self._seq_save()
        self._seq_render()

    def _seq_remove(self, i: int) -> None:
        del self._seq[i]
        self._seq_save()
        self._seq_render()

    def _seq_set_key(self, i: int, k: str) -> None:
        self._seq[i]["tecla"] = k
        self._seq_save()
        self._seq_render()

    def _seq_render(self) -> None:
        for w in self.seq_rows.winfo_children():
            w.destroy()
        if not self._seq:
            ttk.Label(self.seq_rows, text="(nenhuma tecla — use '+ Adicionar tecla')", foreground="#666").grid(row=0, column=0)
        for i, step in enumerate(self._seq):
            ttk.Label(self.seq_rows, text=f"{i + 1}.").grid(row=i, column=0, padx=(0, 6), pady=2)
            lbl = ttk.Label(self.seq_rows, text=(step.get("tecla") or "—").upper(), width=8, relief="groove",
                            anchor="center", font=("Segoe UI", 10, "bold"))
            lbl.grid(row=i, column=1, pady=2)
            btn = ttk.Button(self.seq_rows, text="Alterar")
            btn.grid(row=i, column=2, padx=6)
            btn.config(command=lambda i=i, lbl=lbl, btn=btn: (lbl.config(text="aperte…"),
                       self.capture_key(lambda k, i=i: self._seq_set_key(i, k), btn, self._seq_render)))
            var = tk.StringVar(value=f"{step.get('espera_ms', 0):g}")
            if i < len(self._seq) - 1:
                ttk.Label(self.seq_rows, text="espera").grid(row=i, column=3, padx=(8, 4))
                ttk.Spinbox(self.seq_rows, from_=0, to=60000, increment=50, width=7, textvariable=var).grid(row=i, column=4)
                ttk.Label(self.seq_rows, text="ms").grid(row=i, column=5, padx=(4, 8))

                def save(*_, i=i, var=var):
                    try:
                        v = int(float(var.get().replace(",", ".")))
                    except ValueError:
                        return
                    if 0 <= v <= 60000:
                        self._seq[i]["espera_ms"] = v
                        self._seq_save()
                var.trace_add("write", save)
            ttk.Button(self.seq_rows, text="Remover", command=lambda i=i: self._seq_remove(i)).grid(row=i, column=6)

    # ---------------------------------------------------------------- teste visual
    def test_distances(self) -> None:
        """Captura a área de busca agora e mostra onde estão o pokémon e as sprites, e quais passam do limite."""
        region = self.cfg.get("regiao_sprite")
        if not region:
            self.test_lbl.config(text="Defina a 'Área de busca da sprite' na aba sprites/capture primeiro.")
            return
        tmpl = vision.SpriteTemplate.from_file(SPRITE_PATH)
        if tmpl is None:
            self.test_lbl.config(text="Selecione a sprite de parada na aba sprites/capture primeiro.")
            return
        try:
            name = NameTemplate.from_file(POKEMON_PATH)
        except ValueError as exc:
            self.test_lbl.config(text=str(exc))
            return
        if name is None:
            self.test_lbl.config(text="Selecione o nome do seu pokémon (painel acima) primeiro.")
            return
        frame = capture.grab(region)
        hits, _ = vision.find_sprites(frame, tmpl, self.cfg["sprite_limiar"])
        ref = locate_pokemon(frame, name, self.cfg["pokemon_limiar"])
        if ref is None:
            self.test_lbl.config(text=f"Nome do pokémon NÃO encontrado na área de busca ({len(hits)} sprite(s) de parada "
                                      "detectada(s)). Use 'Testar posição na área de busca' no painel acima.")
            return
        limit = float(self.cfg["shooter_distancia_px"])
        name_pos = ref
        ref_dy = float(self.cfg.get("shooter_ref_dy_px", 0) or 0)
        aim_dy = float(self.cfg.get("shooter_mira_dy_px", 0) or 0)
        ref = (name_pos[0], name_pos[1] + ref_dy)           # centro do círculo = nome + ajuste para baixo
        dists = [math.hypot(x - ref[0], y - ref[1]) for x, y, _ in hits]
        far = [d for d in dists if d > limit]
        txt = (f"Centro do círculo em ({region[0] + ref[0]:.0f}, {region[1] + ref[1]:.0f}) na tela "
               f"(nome + {ref_dy:.0f} px para baixo). "
               f"{len(hits)} sprite(s) de parada: {len(far)} além de {limit:.0f} px")
        txt += f" (a mais distante: {max(far):.0f} px)." if far else (
            f" (a mais distante: {max(dists):.0f} px)." if dists else ".")
        self.test_lbl.config(text=txt)
        self._show_preview(frame, ref, name_pos, hits, dists, limit, tmpl, aim_dy)

    def _show_preview(self, frame, ref, name_pos, hits, dists, limit, tmpl, aim_dy=0.0) -> None:
        img = frame.copy()
        h, w = img.shape[:2]
        s = min(1100 / w, 700 / h, 1.0)                 # a janela encolhe a imagem: engrossa traços e texto na mesma medida
        th, fs = max(1, round(1 / s)), 0.45 / s
        rx, ry = int(round(ref[0])), int(round(ref[1]))
        cv2.circle(img, (rx, ry), int(round(limit)), REF, th)
        cv2.drawMarker(img, (rx, ry), REF, cv2.MARKER_CROSS, int(12 / s), th)
        nx, ny = int(round(name_pos[0])), int(round(name_pos[1]))     # onde o nome foi achado (amarelo) -> centro ajustado
        if (nx, ny) != (rx, ry):
            cv2.drawMarker(img, (nx, ny), NAME, cv2.MARKER_DIAMOND, int(10 / s), th)
            cv2.line(img, (nx, ny), (rx, ry), NAME, th)
        for (x, y, _), d in zip(hits, dists):
            col = OUTSIDE if d > limit else INSIDE
            x0, y0 = int(round(x - tmpl.w / 2)) - 3, int(round(y - tmpl.h / 2)) - 3
            cv2.rectangle(img, (x0, y0), (x0 + tmpl.w + 6, y0 + tmpl.h + 6), col, th)
            if aim_dy:                                              # onde o clique do tiro vai cair
                cv2.drawMarker(img, (int(round(x)), int(round(y + aim_dy))), AIM, cv2.MARKER_TILTED_CROSS, int(12 / s), th)
            pos = (x0, max(12, y0 - 4))
            cv2.putText(img, f"{d:.0f}", pos, cv2.FONT_HERSHEY_SIMPLEX, fs, (0, 0, 0), th + 2, cv2.LINE_AA)
            cv2.putText(img, f"{d:.0f}", pos, cv2.FONT_HERSHEY_SIMPLEX, fs, col, th, cv2.LINE_AA)
        if self._preview_win is not None and self._preview_win.winfo_exists():
            self._preview_win.destroy()
        win = tk.Toplevel(self)
        win.title(f"Distâncias até o pokémon (círculo = {limit:.0f} px; verde dentro, vermelho além; amarelo = nome; azul = clique)")
        self._preview_win = win
        photo = to_photo(Image.fromarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB)), 1100, 700, max_scale=1)
        lbl = ttk.Label(win, image=photo)
        lbl.image = photo                                # evita o garbage collector
        lbl.pack(padx=8, pady=8)
