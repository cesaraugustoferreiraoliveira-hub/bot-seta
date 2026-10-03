"""Página 'engine': liga/desliga o bot (atalho configurável), mostra o estado e o log de decisões."""
from __future__ import annotations
import os
import queue
import subprocess
import sys
import threading
import time
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from core import keys as kb
from core.botlog import (INFO, ALERTA, BOLA, DECISAO, DETECCAO, ERRO, PAROU, RETOMOU, TIRO, TRAVADO, BotLog)
from core.config import ConfigStore
from core.runner import BotRunner

MAX_VIEW_LINES = 2000
TAG_COLORS = {DETECCAO: "#1a5fb4", PAROU: "#c01c28", RETOMOU: "#26a269", DECISAO: "#444444",
              TRAVADO: "#e5760a", ALERTA: "#e5760a", ERRO: "#c01c28", TIRO: "#a51d9b", BOLA: "#b8860b"}


class EnginePage(ttk.Frame):
    def __init__(self, master, cfg: ConfigStore, log: BotLog):
        super().__init__(master)
        self.cfg, self.log = cfg, log
        self._status = ""
        self._q: queue.SimpleQueue = queue.SimpleQueue()   # eventos vindos de outras threads
        self._hotkey_handle = None
        self._capturing = False
        self._seq, self._epoch = 0, log.epoch
        self.runner = BotRunner(cfg, on_status=self._set_status, log=log)

        self._build_activation()
        self._build_speed()
        self._build_log()
        self._register_hotkey(cfg["atalho_ativar"])
        self.after(150, self._poll)

    # ---------------------------------------------------------------- montagem da tela
    def _build_activation(self) -> None:
        box = ttk.LabelFrame(self, text=" Ativação ")
        box.grid(row=0, column=0, sticky="ew", padx=10, pady=(8, 4))
        box.columnconfigure(3, weight=1)

        ttk.Label(box, text="Atalho para ligar/desligar o bot:").grid(row=0, column=0, padx=10, pady=8, sticky="w")
        self.key_lbl = ttk.Label(box, text="", font=("Segoe UI", 10, "bold"), width=14, relief="groove", anchor="center")
        self.key_lbl.grid(row=0, column=1, padx=4)
        self.key_btn = ttk.Button(box, text="Alterar tecla", command=self.change_hotkey)
        self.key_btn.grid(row=0, column=2, padx=6)
        ttk.Button(box, text="Ligar / desligar agora", command=self.toggle).grid(row=0, column=3, padx=6, sticky="e")

        self.dot = tk.Canvas(box, width=22, height=22, highlightthickness=0)
        self.dot.grid(row=1, column=0, sticky="e", padx=(10, 0), pady=(0, 8))
        self._dot_item = self.dot.create_oval(3, 3, 19, 19, fill="#c01c28", outline="")
        self.state_lbl = ttk.Label(box, text="BOT DESATIVADO", font=("Segoe UI", 11, "bold"), foreground="#c01c28")
        self.state_lbl.grid(row=1, column=1, columnspan=2, sticky="w", padx=4, pady=(0, 8))
        self.detail_lbl = ttk.Label(box, text="", foreground="#555")
        self.detail_lbl.grid(row=1, column=3, sticky="w", padx=6, pady=(0, 8))

        ttk.Label(box, text="Sentido da rota:").grid(row=2, column=0, padx=10, pady=(0, 8), sticky="w")
        self.dir_var = tk.StringVar(value=self.cfg["sentido"])
        dirs = ttk.Frame(box)
        dirs.grid(row=2, column=1, columnspan=3, sticky="w", padx=4, pady=(0, 8))
        for text, value in (("Horário", "horario"), ("Anti-horário", "antihorario")):
            ttk.Radiobutton(dirs, text=text, value=value, variable=self.dir_var,
                            command=lambda: self.cfg.set("sentido", self.dir_var.get())).pack(side="left", padx=(0, 14))
        ttk.Label(dirs, text="(vale na hora, mesmo com o bot ligado)", foreground="#666").pack(side="left")

        ttk.Label(box, text="Teclas de movimento:").grid(row=3, column=0, padx=10, pady=(0, 8), sticky="w")
        self.layout_cb = ttk.Combobox(box, values=["WASD", "Setas"], state="readonly", width=12)
        self.layout_cb.set("Setas" if self.cfg.get("teclas_movimento") == "setas" else "WASD")
        self.layout_cb.grid(row=3, column=1, padx=4, pady=(0, 8))
        self.layout_cb.bind("<<ComboboxSelected>>", lambda e: self.cfg.set(
            "teclas_movimento", "setas" if self.layout_cb.get() == "Setas" else "wasd"))
        ttk.Label(box, text="Método de envio:").grid(row=3, column=2, padx=(10, 4), pady=(0, 8), sticky="e")
        self.method_cb = ttk.Combobox(box, values=list(kb.METHODS), state="readonly", width=12)
        self.method_cb.set(self.cfg.get("metodo_teclas", "scancode"))
        self.method_cb.grid(row=3, column=3, padx=4, pady=(0, 8), sticky="w")
        self.method_cb.bind("<<ComboboxSelected>>", lambda e: self.cfg.set("metodo_teclas", self.method_cb.get()))

        ttk.Label(box, text="Tolerância de travamento:").grid(row=4, column=0, padx=10, pady=(0, 8), sticky="w")
        trow = ttk.Frame(box)
        trow.grid(row=4, column=1, columnspan=3, sticky="w", padx=4, pady=(0, 8))
        self.stuck_ms = tk.StringVar(value=str(int(round(float(self.cfg["timeout_travado_s"]) * 1000))))
        ttk.Spinbox(trow, from_=50, to=60000, increment=50, width=7, textvariable=self.stuck_ms).pack(side="left")
        ttk.Label(trow, text="ms").pack(side="left", padx=(4, 10))
        ttk.Label(trow, text="tempo com a seta parada antes de tentar outra tecla (vale na hora)",
                  foreground="#666").pack(side="left")
        self.stuck_ms.trace_add("write", lambda *a: self._save_stuck_ms())

        ttk.Label(box, text="Abertura das curvas:").grid(row=5, column=0, padx=10, pady=(0, 8), sticky="w")
        arow = ttk.Frame(box)
        arow.grid(row=5, column=1, columnspan=3, sticky="w", padx=4, pady=(0, 8))
        self.abertura_pct = tk.StringVar(value=str(int(round(float(self.cfg.get("abertura_curva", 0.75)) * 100))))
        ttk.Spinbox(arow, from_=50, to=90, increment=5, width=7, textvariable=self.abertura_pct).pack(side="left")
        ttk.Label(arow, text="%").pack(side="left", padx=(4, 10))
        ttk.Label(arow, text="50 = meio do corredor; 75 = curvas abertas, longe da parede interna (vale ao ligar o bot)",
                  foreground="#666").pack(side="left")
        self.abertura_pct.trace_add("write", lambda *a: self._save_abertura())
        self._show_hotkey()

    def _build_speed(self) -> None:
        """Velocidade conforme a quantidade de sprites na tela (vale na hora, mesmo com o bot ligado)."""
        box = ttk.LabelFrame(self, text=" Velocidade conforme as sprites na tela ")
        box.grid(row=1, column=0, sticky="ew", padx=10, pady=4)
        self.vel_on = tk.BooleanVar(value=bool(self.cfg.get("vel_ativo", True)))
        ttk.Checkbutton(box, text="Ajustar a velocidade pela quantidade de sprites (desligado = passo fixo de antes)",
                        variable=self.vel_on,
                        command=lambda: self.cfg.set("vel_ativo", bool(self.vel_on.get()))
                        ).grid(row=0, column=0, columnspan=5, sticky="w", padx=10, pady=(6, 4))
        for col, text in ((1, "Sprites na tela"), (3, "Passo (ms)"), (4, "Previsão (ms)")):
            ttk.Label(box, text=text, foreground="#555").grid(row=1, column=col, padx=6, sticky="w")

        def spin(parent, key, hi, step, row, col):
            var = tk.StringVar(value=str(int(self.cfg.get(key, 0))))
            ttk.Spinbox(parent, from_=0, to=hi, increment=step, width=6, textvariable=var).grid(
                row=row, column=col, padx=6, pady=2, sticky="w")

            def save(*_):
                try:
                    v = int(float(var.get().replace(",", ".")))
                except ValueError:
                    return  # campo vazio durante a digitação
                if 0 <= v <= hi:
                    self.cfg.set(key, v)
                    self._refresh_speed_labels()
            var.trace_add("write", save)
            return var

        self._vel_lbl = {}
        for r, (nome, chave) in enumerate((("Muito rápido", "muito_rapido"), ("Rápido", "rapido"),
                                           ("Devagar", "devagar")), start=2):
            ttk.Label(box, text=nome, font=("Segoe UI", 9, "bold")).grid(row=r, column=0, padx=(10, 6), sticky="w")
            if chave == "devagar":
                self._vel_lbl[chave] = ttk.Label(box, text="")
                self._vel_lbl[chave].grid(row=r, column=1, padx=6, sticky="w")
            else:
                cell = ttk.Frame(box)
                cell.grid(row=r, column=1, sticky="w")
                ttk.Label(cell, text="até").pack(side="left", padx=(6, 4))
                var = tk.StringVar(value=str(int(self.cfg.get(f"vel_{chave}_ate", 0))))
                ttk.Spinbox(cell, from_=0, to=99, increment=1, width=4, textvariable=var).pack(side="left")
                self._vel_lbl[chave] = ttk.Label(cell, text="")
                self._vel_lbl[chave].pack(side="left", padx=(4, 0))

                def save_ate(*_, key=f"vel_{chave}_ate", v=var):
                    try:
                        n = int(float(v.get().replace(",", ".")))
                    except ValueError:
                        return
                    if 0 <= n <= 99:
                        self.cfg.set(key, n)
                        self._refresh_speed_labels()
                var.trace_add("write", save_ate)
            spin(box, f"vel_{chave}_passo_ms", 2000, 10, r, 3)
            spin(box, f"vel_{chave}_previsao_ms", 500, 10, r, 4)
        ttk.Label(box, justify="left", foreground="#666", text=(
            "Passo 0 = segura a tecla sem soltar entre os ciclos (a seta não para entre um passo e outro; só troca a tecla "
            "quando a direção muda).\nPasso > 0 = segura por esse tempo e solta. Previsão = quanto à frente (ms) o bot "
            "projeta a posição da seta para virar na hora certa (0 = desligada).\nDica: com tecla contínua, baixe a "
            "'Tolerância de travamento' (ex.: 300 ms) para a seta não ficar empurrando a parede.")
        ).grid(row=5, column=0, columnspan=5, sticky="w", padx=10, pady=(4, 8))
        self._refresh_speed_labels()

    def _refresh_speed_labels(self) -> None:
        if not hasattr(self, "_vel_lbl"):
            return
        mr, r = int(self.cfg.get("vel_muito_rapido_ate", 0)), int(self.cfg.get("vel_rapido_ate", 2))
        for chave in ("muito_rapido", "rapido"):
            self._vel_lbl[chave].config(text="sprite(s)")
        self._vel_lbl["devagar"].config(text=f"acima de {max(mr, r)} sprites")

    def _save_abertura(self) -> None:
        try:
            v = float(self.abertura_pct.get().replace(",", ".")) / 100.0
        except ValueError:
            return  # campo vazio durante a digitação
        if 0.5 <= v <= 0.9:
            self.cfg.set("abertura_curva", v)

    def _build_log(self) -> None:
        box = ttk.LabelFrame(self, text=" Log da engine ")
        box.grid(row=2, column=0, sticky="nsew", padx=10, pady=(4, 8))
        box.columnconfigure(0, weight=1)
        box.rowconfigure(1, weight=1)
        self.rowconfigure(2, weight=1)
        self.columnconfigure(0, weight=1)

        self.summary = ttk.Label(box, text="", foreground="#555")
        self.summary.grid(row=0, column=0, columnspan=2, sticky="w", padx=8, pady=(6, 2))

        self.text = tk.Text(box, width=96, height=15, wrap="none", state="disabled", font=("Consolas", 9),
                            background="#fafafa")
        sb = ttk.Scrollbar(box, command=self.text.yview)
        self.text.configure(yscrollcommand=sb.set)
        self.text.grid(row=1, column=0, sticky="nsew", padx=(8, 0))
        sb.grid(row=1, column=1, sticky="ns", padx=(0, 8))
        for kind, color in TAG_COLORS.items():
            self.text.tag_configure(kind, foreground=color)

        bar = ttk.Frame(box)
        bar.grid(row=2, column=0, columnspan=2, sticky="ew", padx=8, pady=6)
        ttk.Button(bar, text="Abrir log", command=self.open_log).pack(side="left")
        ttk.Button(bar, text="Salvar log em…", command=self.save_log).pack(side="left", padx=6)
        ttk.Button(bar, text="Resetar log", command=self.reset_log).pack(side="left")
        ttk.Button(bar, text="Testar teclas (3s)", command=self.test_keys).pack(side="left", padx=6)
        self.verbose = tk.BooleanVar(value=bool(self.cfg.get("log_detalhado")))
        ttk.Checkbutton(bar, text="Log detalhado (registrar toda checagem)", variable=self.verbose,
                        command=lambda: self.cfg.set("log_detalhado", bool(self.verbose.get()))).pack(side="right")

    def _save_stuck_ms(self) -> None:
        try:
            ms = int(float(self.stuck_ms.get().replace(",", ".")))
        except ValueError:
            return  # campo vazio ou incompleto durante a digitação
        if ms >= 50:
            self.cfg.set("timeout_travado_s", ms / 1000)

    # ---------------------------------------------------------------- atalho global
    def _show_hotkey(self) -> None:
        self.key_lbl.config(text=str(self.cfg["atalho_ativar"]).upper())

    def _register_hotkey(self, key: str) -> bool:
        try:
            import keyboard  # só existe/funciona onde há atalhos globais (Windows)
            self._unregister_hotkey()
            self._hotkey_handle = keyboard.add_hotkey(key, lambda: self._q.put(("toggle", None)))
            return True
        except Exception as exc:  # noqa: BLE001
            self.detail_lbl.config(text=f"Atalho '{key}' indisponível: {exc}")
            return False

    def _unregister_hotkey(self) -> None:
        if self._hotkey_handle is not None:
            try:
                import keyboard
                keyboard.remove_hotkey(self._hotkey_handle)
            except Exception:  # noqa: BLE001
                pass
            self._hotkey_handle = None

    def change_hotkey(self) -> None:
        """Espera o usuário apertar a nova tecla (ou combinação). Esc cancela."""
        if self._capturing:
            return
        self._capturing = True
        self._unregister_hotkey()  # enquanto captura, o atalho antigo não pode disparar o bot
        self.key_btn.config(state="disabled")
        self.key_lbl.config(text="aperte a tecla…")

        def worker():
            try:
                import keyboard
                new = keyboard.read_hotkey(suppress=False)
            except Exception as exc:  # noqa: BLE001
                self._q.put(("hotkey_err", str(exc)))
                return
            self._q.put(("hotkey_new", new))

        threading.Thread(target=worker, daemon=True).start()

    def _finish_capture(self, new: str | None) -> None:
        old = self.cfg["atalho_ativar"]
        if new and new.lower() != "esc":
            if self._register_hotkey(new):
                self.cfg.set("atalho_ativar", new)
                self.log.add("INFO", f"Atalho de ativação alterado: {old.upper()} -> {new.upper()}")
            else:
                self._register_hotkey(old)
        else:
            self._register_hotkey(old)
        self._capturing = False
        self.key_btn.config(state="normal")
        self._show_hotkey()

    # ---------------------------------------------------------------- ligar / desligar
    def toggle(self) -> None:
        if self.runner.stopping:
            return
        if self.runner.running:
            self.runner.stop()
        else:
            self.log.session_header()
            self._status = "Iniciando…"
            self.runner.start()

    def _set_status(self, msg: str) -> None:  # chamado pela thread do bot
        self._status = msg

    def shutdown(self) -> None:
        self.runner.stop()
        self._unregister_hotkey()

    def test_keys(self) -> None:
        """Abra o Bloco de Notas, clique nele e use este botão: testa os 4 métodos de envio, um por tecla
        (scancode=W, vk=A, keybd_event=S, pyautogui=D) e confere se o Windows registrou cada uma."""
        if self.runner.running:
            messagebox.showinfo("Testar teclas", "Desligue o bot antes de testar.", parent=self)
            return
        self.log.add(INFO, "Teste de teclas: clique na janela de destino (ex.: Bloco de Notas), começa em 3s")

        def worker():
            old = (self.cfg.get("metodo_teclas", "scancode"), self.cfg.get("teclas_movimento", "wasd"))
            try:
                kb.set_layout("wasd")
                time.sleep(3)
                self.log.add(INFO, f"Janela em foco: {kb.foreground_title()!r} | administrador: {kb.is_admin()}")
                for (method, label), key in zip(kb.METHODS.items(), "wasd"):
                    kb.set_method(method)
                    try:
                        kb.key_down(key)
                        time.sleep(0.2)
                        down = kb.is_down(key)
                        kb.key_up(key)
                        estado = {True: "registrada pelo Windows", False: "NÃO registrada pelo Windows",
                                  None: "(sem verificação)"}[down]
                        self.log.add(INFO, f"Teste {label}: enviou {key.upper()} -> {estado}")
                    except Exception as exc:  # noqa: BLE001
                        self.log.add(ERRO, f"Teste {label}: falhou -> {exc}")
                    time.sleep(0.5)
                self.log.add(INFO, "Fim do teste. Quais letras apareceram no Bloco de Notas? "
                                   "(w=scancode, a=vk, s=keybd_event, d=pyautogui)")
            finally:
                kb.release_all()
                kb.set_method(old[0])
                kb.set_layout(old[1])

        threading.Thread(target=worker, daemon=True).start()

    # ---------------------------------------------------------------- log: abrir / salvar / resetar
    def open_log(self) -> None:
        path = self.log.ensure_file()
        try:
            if sys.platform == "win32":
                os.startfile(path)  # noqa: S606
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(path)])
            else:
                subprocess.Popen(["xdg-open", str(path)])
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("Abrir log", f"Não consegui abrir o arquivo:\n{path}\n\n{exc}", parent=self)

    def save_log(self) -> None:
        start = self.cfg.get("log_pasta_salvar")
        dest = filedialog.asksaveasfilename(
            parent=self, title="Salvar log", defaultextension=".txt",
            initialdir=start if start and Path(start).is_dir() else None,
            initialfile=f"log_bot_{datetime.now():%Y%m%d_%H%M%S}.txt",
            filetypes=[("Texto", "*.txt"), ("Log", "*.log"), ("Todos", "*.*")])
        if not dest:
            return
        try:
            self.log.save_as(dest)
            self.cfg.set("log_pasta_salvar", str(Path(dest).parent))
        except OSError as exc:
            messagebox.showerror("Salvar log", f"Não foi possível salvar:\n{exc}", parent=self)

    def reset_log(self) -> None:
        if messagebox.askyesno("Resetar log", "Apagar todo o log (tela e arquivo)?", parent=self):
            self.log.reset()

    # ---------------------------------------------------------------- atualização periódica da tela
    def _poll(self) -> None:
        try:
            while True:
                kind, val = self._q.get_nowait()
                if kind == "toggle" and not self._capturing:
                    self.toggle()
                elif kind == "hotkey_new":
                    self._finish_capture(val)
                elif kind == "hotkey_err":
                    self._finish_capture(None)
                    self.detail_lbl.config(text=f"Erro ao capturar tecla: {val}")
        except queue.Empty:
            pass

        self._refresh_state()
        self._refresh_log()
        self.after(150, self._poll)

    def _refresh_state(self) -> None:
        on = self.runner.running
        color = "#26a269" if on else "#c01c28"
        self.dot.itemconfig(self._dot_item, fill=color)
        self.state_lbl.config(text="BOT ATIVO" if on else "BOT DESATIVADO", foreground=color)
        if on:
            self.detail_lbl.config(text=("Desligando…" if self.runner.stopping else self._status))
        elif self._status.startswith("Erro"):
            self.detail_lbl.config(text=self._status)
        elif not self.detail_lbl.cget("text").startswith(("Atalho", "Erro")):
            self.detail_lbl.config(text="")

    def _refresh_log(self) -> None:
        if self.log.epoch != self._epoch:  # log foi resetado
            self._epoch, self._seq = self.log.epoch, 0
            self.text.config(state="normal")
            self.text.delete("1.0", "end")
            self.text.config(state="disabled")
        new, self._seq, _ = self.log.since(self._seq)
        if new:
            at_end = self.text.yview()[1] >= 0.999
            self.text.config(state="normal")
            for kind, line in new:
                self.text.insert("end", line + "\n", kind)
            extra = int(self.text.index("end-1c").split(".")[0]) - MAX_VIEW_LINES
            if extra > 0:
                self.text.delete("1.0", f"{extra + 1}.0")
            self.text.config(state="disabled")
            if at_end:
                self.text.see("end")
        c = self.log.counts
        self.summary.config(text=f"Detecções: {c[DETECCAO]}   Paradas: {c[PAROU]}   Retomadas: {c[RETOMOU]}   "
                                 f"Tiros: {c[TIRO]}   Bolas: {c[BOLA]}   Travamentos: {c[TRAVADO]}   Decisões: {c[DECISAO]}   Alertas/erros: {c[ALERTA] + c[ERRO]}")
