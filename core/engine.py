"""Motor de decisão: dado onde a seta está, anda ou não? E qual(is) tecla(s) apertar?

Não captura tela nem aperta teclas: só decide. Isso o torna testável e independente da GUI.
"""
from __future__ import annotations
import math
import time
import numpy as np

# direção (dx, dy) em pixels do mapa (y para baixo) -> teclas
DIRECTIONS = {
    (0, -1): ["w"], (0, 1): ["s"], (-1, 0): ["a"], (1, 0): ["d"],
    (1, -1): ["d", "w"], (1, 1): ["d", "s"], (-1, -1): ["a", "w"], (-1, 1): ["a", "s"],
}


def should_halt(sprite_count: int, limit: int) -> bool:
    """True quando há sprites suficientes na tela para a seta ficar parada."""
    return sprite_count >= limit


class MovementEngine:
    def __init__(self, walk: np.ndarray, loop: np.ndarray, *, lookahead: int = 12,
                 probe_px: int = 4, stuck_timeout_s: float = 0.6, min_move_px: float = 0.7):
        self.walk = walk
        self.loop = loop
        self.lookahead = lookahead
        self.probe_px = probe_px
        self.stuck_timeout_s = stuck_timeout_s
        self.min_move_px = min_move_px
        self.reset()

    def reset(self) -> None:
        self._last_pos = None
        self._last_move_t = time.time()
        self.stuck_level = 0

    # ---------------------------------------------------------------- alvo e ranking
    def target_for(self, pos):
        near = int(np.argmin(np.hypot(self.loop[:, 0] - pos[0], self.loop[:, 1] - pos[1])))
        return self.loop[(near + self.lookahead) % len(self.loop)]

    def ranked_commands(self, pos, target) -> list[list[str]]:
        """Comandos ordenados do melhor ao pior: alinhados ao alvo e sem bater em obstáculo."""
        vx, vy = target[0] - pos[0], target[1] - pos[1]
        n = math.hypot(vx, vy) or 1.0
        vx, vy = vx / n, vy / n
        hgt, wid = self.walk.shape
        scored = []
        for (dx, dy), keys in DIRECTIONS.items():
            d = math.hypot(dx, dy)
            score = (dx * vx + dy * vy) / d
            px = int(round(pos[0] + dx / d * self.probe_px))
            py = int(round(pos[1] + dy / d * self.probe_px))
            if not (0 <= py < hgt and 0 <= px < wid) or not self.walk[py, px]:
                score -= 2.0
            scored.append((score, keys))
        scored.sort(key=lambda t: -t[0])
        return [k for _, k in scored]

    # ---------------------------------------------------------------- decisão
    def decide(self, pos, now: float | None = None) -> list[str]:
        """Teclas a pressionar agora. Se a seta não saiu do lugar dentro do tempo limite,
        passa para o próximo melhor comando (ex.: D -> D+S -> D+W ...)."""
        now = time.time() if now is None else now
        if self._last_pos is None or math.hypot(pos[0] - self._last_pos[0],
                                                pos[1] - self._last_pos[1]) >= self.min_move_px:
            self._last_pos, self._last_move_t, self.stuck_level = pos, now, 0
        elif now - self._last_move_t > self.stuck_timeout_s:
            self.stuck_level += 1
            self._last_move_t = now
        cmds = self.ranked_commands(pos, self.target_for(pos))
        return cmds[self.stuck_level % len(cmds)]

    def notify_paused(self, now: float | None = None) -> None:
        """Chame enquanto a seta está parada de propósito, para não contar como travamento."""
        self._last_move_t = time.time() if now is None else now
