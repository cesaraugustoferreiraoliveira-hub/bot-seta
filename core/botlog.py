"""Log da engine: guarda na memória (para a UI) e grava em arquivo em tempo real."""
from __future__ import annotations
import threading
from collections import Counter, deque
from datetime import datetime
from pathlib import Path

from .config import LOG_PATH

# tipos de evento usados pelo runner (a UI colore por tipo)
INFO, DETECCAO, PAROU, RETOMOU, DECISAO, TRAVADO, ALERTA, ERRO = (
    "INFO", "DETECÇÃO", "PAROU", "RETOMOU", "DECISÃO", "TRAVADO", "ALERTA", "ERRO")
TIRO = "TIRO"   # shooter: tecla acionada sobre uma sprite distante que parou


class BotLog:
    def __init__(self, path: Path = LOG_PATH, max_lines: int = 5000):
        self.path = Path(path)
        self._lock = threading.Lock()
        self._entries: deque[tuple[int, str, str]] = deque(maxlen=max_lines)  # (seq, tipo, linha)
        self._seq = 0
        self.epoch = 0                      # sobe a cada reset (a UI usa para limpar a tela)
        self.counts: Counter[str] = Counter()

    def add(self, kind: str, msg: str) -> None:
        line = f"[{datetime.now():%H:%M:%S.%f}"[:-3] + f"] {kind:<9} {msg}"
        with self._lock:
            self._seq += 1
            self._entries.append((self._seq, kind, line))
            self.counts[kind] += 1
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with self.path.open("a", encoding="utf-8") as f:
                    f.write(line + "\n")
            except OSError:
                pass  # log em arquivo é best-effort; a memória continua valendo

    def session_header(self) -> None:
        self.add(INFO, f"===== Sessão iniciada em {datetime.now():%d/%m/%Y %H:%M:%S} =====")

    def since(self, seq: int) -> tuple[list[tuple[str, str]], int, int]:
        """Entradas novas depois de `seq`. Retorna (lista de (tipo, linha), novo_seq, epoch)."""
        with self._lock:
            new = [(k, ln) for s, k, ln in self._entries if s > seq]
            return new, self._seq, self.epoch

    def text(self) -> str:
        with self._lock:
            return "\n".join(ln for _, _, ln in self._entries) + ("\n" if self._entries else "")

    def save_as(self, path) -> None:
        Path(path).write_text(self.text(), encoding="utf-8")

    def reset(self) -> None:
        with self._lock:
            self._entries.clear()
            self.counts.clear()
            self._seq = 0
            self.epoch += 1
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                self.path.write_text("", encoding="utf-8")
            except OSError:
                pass

    def ensure_file(self) -> Path:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.touch(exist_ok=True)
        return self.path
