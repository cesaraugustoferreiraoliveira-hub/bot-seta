"""Ciclo R -> E: a regra que o revive precisa cumprir, garantida num único lugar.

Regras (todas conferidas NO MOMENTO de cada envio da tecla, não só uma vez antes):
  1. o E só existe dentro de um ciclo aberto pelo shooter (quando todas as sprites reconhecidas ficam dentro da
     distância limite);
  2. o E só pode ser apertado depois que o R do MESMO ciclo foi executado (`mark_r_done`, chamado só quando o envio
     do R terminou sem erro);
  3. o E só pode ser apertado depois de `gap_s` (padrão 0,8 s, editável) contados a partir do fim do R;
  4. o E é um toque rápido; se não for confirmado, só pode ser tocado de novo depois de `retry_gap_s` (nunca dois
     ao mesmo tempo: o revive tem custo). Depois de encerrado ou cancelado, o ciclo não aperta nunca mais;
  5. se o bot for desligado (ciclo cancelado), nenhum E sai a partir daquele instante.

Além disso, a tecla do revive fica RESERVADA em core/keys.py: qualquer outro caminho que tente apertá-la
(sequência do shooter, habilidades por vida, código futuro) recebe PermissionError. A única porta é `revive_press()`.

Sem tela, sem teclado e sem GUI: o relógio é injetável (os testes usam um falso).
"""
from __future__ import annotations
import contextlib
import threading
import time

from . import keys as kb

DEFAULT_GAP_S = 0.8    # intervalo padrão entre o fim do R e o primeiro E (editável na página revive; 0 = sem espera)


def effective_gap(configured) -> float:
    """Intervalo R -> E em segundos: o configurado (>= 0); valor inválido volta ao padrão."""
    try:
        return max(0.0, float(configured))
    except (TypeError, ValueError):
        return DEFAULT_GAP_S


class ComboBlocked(Exception):
    """O E não pode ser apertado agora (o texto diz por quê)."""


class Cycle:
    """Um ciclo R -> E. Criado pelo shooter; passado ao revive, que só aperta o E através dele."""

    def __init__(self, revive_key: str, gap_s: float = DEFAULT_GAP_S, clock=time.monotonic, retry_gap_s: float = 0.0):
        self.revive_key = revive_key
        self.gap_s = float(gap_s)
        self.retry_gap_s = float(retry_gap_s)     # intervalo MÍNIMO entre dois apertos do E (o revive tem custo: nunca dois ao mesmo tempo)
        self._clock = clock
        self._lock = threading.Lock()
        self.cancel_event = threading.Event()
        self._r_done_at: float | None = None
        self._e_first_at: float | None = None
        self._e_last_at: float | None = None
        self.presses = 0                          # quantas vezes o E foi apertado neste ciclo
        self._finished = False

    # ---------------------------------------------------------------- estado
    @property
    def cancelled(self) -> bool:
        return self.cancel_event.is_set()

    @property
    def r_done(self) -> bool:
        return self._r_done_at is not None

    @property
    def e_gap_s(self) -> float | None:
        """Quanto tempo se passou entre o fim do R e o primeiro E (None se o E ainda não saiu)."""
        if self._r_done_at is None or self._e_first_at is None:
            return None
        return self._e_first_at - self._r_done_at

    def cancel(self) -> None:
        with self._lock:                 # espera um envio do E em andamento terminar; depois dele, nenhum outro sai
            self.cancel_event.set()

    def finish(self) -> None:
        """O E deste ciclo acabou (confirmado ou não): o ciclo não serve mais para nada."""
        with self._lock:
            self._finished = True

    def mark_r_done(self) -> None:
        """O R foi executado (envio concluído sem erro). Começa a contar o intervalo até o E.
        É o ÚNICO ponto onde uma verificação extra do R (ex.: sprite da pokebar) precisa ser ligada."""
        with self._lock:
            if self._r_done_at is None and not self.cancelled:
                self._r_done_at = self._clock()

    # ---------------------------------------------------------------- a porta do E
    def _why_blocked(self) -> str | None:
        """Motivo pelo qual o E não pode sair agora, ou None se pode. Chamar com o lock."""
        if self.cancelled:
            return "ciclo cancelado (o bot voltou a andar)"
        if self._finished:
            return "este ciclo já usou o E"
        if self._r_done_at is None:
            return "o R ainda não foi executado"
        left = self._r_done_at + self.gap_s - self._clock()
        if left > 0:
            return f"faltam {left:.2f}s do intervalo de {self.gap_s:.2f}s depois do R"
        if self._e_last_at is not None:
            left = self._e_last_at + self.retry_gap_s - self._clock()
            if left > 0:
                return f"o E anterior ainda está sendo verificado (faltam {left:.2f}s para poder apertar de novo)"
        return None

    def wait_ready(self) -> bool:
        """Espera (sem apertar nada) até o E poder sair. False se o ciclo foi cancelado/encerrado ou o R não ocorreu."""
        while True:
            with self._lock:
                if self.cancelled or self._finished or self._r_done_at is None:
                    return False
                left = self._r_done_at + self.gap_s - self._clock()
                if self._e_last_at is not None:
                    left = max(left, self._e_last_at + self.retry_gap_s - self._clock())
            if left <= 0:
                return True
            if self.cancel_event.wait(min(left, 0.05)):
                return False

    @contextlib.contextmanager
    def revive_press(self):
        """Use em volta de CADA envio do E (descer ou repetir). Confere as regras e só então libera a tecla
        reservada para esta thread; se alguma regra falhar, levanta ComboBlocked e nada é enviado."""
        with self._lock:
            why = self._why_blocked()
            if why is not None:
                raise ComboBlocked(why)
            now = self._clock()
            if self._e_first_at is None:
                self._e_first_at = now
            self._e_last_at = now
            self.presses += 1
            # o envio acontece ainda dentro do lock: cancelar/encerrar não passa entre a checagem e o envio
            with kb.authorized(self.revive_key):
                yield
