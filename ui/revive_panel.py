"""Módulo da GUI: comando Revive — tecla, sensibilidade da 'foto' e teste de confirmação.

O revive é acionado pelo shooter quando TODAS as sprites reconhecidas estão dentro da distância limite (depois da
sequência de teclas dessa mesma situação, se ela estiver ligada). A tecla fica SEGURADA até a foto mudar.
A lógica fica em core/revive.py.
"""
from __future__ import annotations
import time
import tkinter as tk
from tkinter import ttk

from core import capture
from core.config import ConfigStore
from core.revive import ChangeWatcher
from .fields import key_label, number_row
from .key_capture import KeyCapture

TEST_WAIT_S = 10.0


class RevivePanel(ttk.LabelFrame):
    def __init__(self, master, cfg: ConfigStore, keys: KeyCapture):
        super().__init__(master, text=" Revive ")
        self.cfg, self.keys = cfg, keys
        self.columnconfigure(2, weight=1)

        self.ativo = tk.BooleanVar(value=bool(cfg.get("revive_ativo")))
        ttk.Checkbutton(self, text="Ativar o revive (acionado quando o shooter conclui: TODAS as sprites reconhecidas "
                                   "dentro da distância limite)", variable=self.ativo,
                        command=lambda: cfg.set("revive_ativo", bool(self.ativo.get()))
                        ).grid(row=0, column=0, columnspan=3, sticky="w", padx=10, pady=(8, 4))

        ttk.Label(self, text="Tecla do revive").grid(row=1, column=0, sticky="w", padx=10, pady=3)
        krow = ttk.Frame(self)
        krow.grid(row=1, column=1, sticky="w", pady=3)
        self.key_lbl = key_label(krow, cfg.get("revive_tecla"))
        self.key_lbl.pack(side="left")
        self.key_btn = ttk.Button(krow, text="Alterar tecla", command=self._change_key)
        self.key_btn.pack(side="left", padx=6)
        ttk.Label(self, text="fica SEGURADA até a foto mudar; depois é solta (padrão: E)", foreground="#666").grid(
            row=1, column=2, sticky="w", padx=8)

        number_row(self, 2, cfg, "Sensibilidade da foto", "revive_sens_pct", 0.1, 100, 0.5, "% dos pixels",
                   "quanto da foto precisa mudar para confirmar que o revive foi executado", float)
        number_row(self, 3, cfg, "Tempo máximo segurando", "revive_timeout_s", 0, 600, 1, "s",
                   "desiste e solta a tecla se a foto não mudar; 0 = segura até o bot voltar a andar", float)

        ttk.Label(self, text="A foto é guardada na memória ao ligar o bot e de novo a cada revive confirmado.\n"
                             "A área da foto é definida na aba 'Captura' (Local da foto do revive).",
                  foreground="#666", justify="left").grid(row=4, column=0, columnspan=3, sticky="w", padx=10, pady=(4, 2))

        self.test_btn = ttk.Button(self, text=f"Testar confirmação (faça o revive no jogo em até {TEST_WAIT_S:.0f}s)",
                                   command=self.test_confirm)
        self.test_btn.grid(row=5, column=0, columnspan=3, sticky="w", padx=10, pady=(6, 2))
        self.test_lbl = ttk.Label(self, text="", wraplength=640, justify="left")
        self.test_lbl.grid(row=6, column=0, columnspan=3, sticky="w", padx=10, pady=(2, 8))

    # ---------------------------------------------------------------- tecla
    def _change_key(self) -> None:
        def got(k):
            self.cfg.set("revive_tecla", k)
        self.key_lbl.config(text="APERTE…")
        self.keys.capture(got, self.key_btn, lambda: self.key_lbl.config(text=(self.cfg.get("revive_tecla") or "—").upper()),
                          on_error=lambda m: self.test_lbl.config(text=m))

    # ---------------------------------------------------------------- teste da confirmação
    def test_confirm(self) -> None:
        """Guarda a foto agora e espera ela mudar (sem apertar nada: quem executa o revive é você, no jogo)."""
        region = self.cfg.get("regiao_revive_foto")
        if not region:
            self.test_lbl.config(text="Defina o 'Local da foto do revive' na aba Captura primeiro.")
            return
        try:
            watcher = ChangeWatcher()
            watcher.remember(capture.grab(region))
        except Exception as exc:  # noqa: BLE001
            self.test_lbl.config(text=f"Não consegui capturar a foto: {exc}")
            return
        sens = float(self.cfg.get("revive_sens_pct", 2.0))
        t0 = time.time()
        best = [0.0]
        self.test_btn.config(state="disabled")

        def poll():
            try:
                pct = watcher.changed_pct(capture.grab(region))
            except Exception as exc:  # noqa: BLE001
                self.test_btn.config(state="normal")
                self.test_lbl.config(text=f"Falha na captura: {exc}")
                return
            best[0] = max(best[0], pct)
            el = time.time() - t0
            if pct >= sens:
                self.test_btn.config(state="normal")
                self.test_lbl.config(text=f"A foto MUDOU após {el:.1f}s ({pct:.1f}% dos pixels; mínimo {sens:.1f}%): "
                                          "o revive seria confirmado.")
            elif el >= TEST_WAIT_S:
                self.test_btn.config(state="normal")
                self.test_lbl.config(text=f"A foto NÃO mudou em {TEST_WAIT_S:.0f}s (máximo {best[0]:.1f}% dos pixels, "
                                          f"mínimo {sens:.1f}%). Confira a área da foto ou baixe a sensibilidade.")
            else:
                self.test_lbl.config(text=f"Foto guardada. Execute o revive agora… {TEST_WAIT_S - el:.0f}s "
                                          f"(mudança atual {pct:.1f}%)")
                self.after(100, poll)
        poll()
