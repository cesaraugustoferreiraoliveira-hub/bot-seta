"""Motor de decisão: dado onde a seta está, anda ou não? E qual(is) tecla(s) apertar?

Não captura tela nem aperta teclas: só decide. Isso o torna testável e independente da GUI.
"""
from __future__ import annotations
import math
import time
from collections import deque
import numpy as np

# direção (dx, dy) em pixels do mapa (y para baixo) -> teclas
DIRECTIONS = {
    (0, -1): ["w"], (0, 1): ["s"], (-1, 0): ["a"], (1, 0): ["d"],
    (1, -1): ["d", "w"], (1, 1): ["d", "s"], (-1, -1): ["a", "w"], (-1, 1): ["a", "s"],
}


VEL_JANELA_S = 0.4     # janela (s) usada para medir a velocidade da seta
MAX_PREDICT_PX = 8.0   # a projeção da posição nunca passa disto (px do mapa completo)


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
        self._vel = (0.0, 0.0)          # velocidade estimada da seta (px do mapa completo por segundo)
        self._vel_buf: deque = deque()

    # ---------------------------------------------------------------- previsão
    def _update_velocity(self, pos, now: float) -> None:
        """Velocidade por regressão linear das posições dos últimos VEL_JANELA_S segundos. Uma janela curta
        (diferença entre dois quadros) mede só o ruído de ±1 px da localização; a janela longa é estável."""
        if self._vel_buf and now - self._vel_buf[-1][0] > 0.5:
            self._vel_buf.clear()                    # ficou muito tempo sem medir: o histórico não vale mais
        self._vel_buf.append((now, float(pos[0]), float(pos[1])))
        while now - self._vel_buf[0][0] > VEL_JANELA_S:
            self._vel_buf.popleft()
        if len(self._vel_buf) < 4:
            self._vel = (0.0, 0.0)
            return
        t = np.array([p[0] for p in self._vel_buf])
        t -= t.mean()
        den = float((t * t).sum())
        if den < 1e-6:
            self._vel = (0.0, 0.0)
            return
        self._vel = (float((t * np.array([p[1] for p in self._vel_buf])).sum() / den),
                     float((t * np.array([p[2] for p in self._vel_buf])).sum() / den))

    def predict(self, pos, predict_s: float):
        """Onde a seta estará daqui a `predict_s` segundos mantendo a velocidade medida (limitado e sempre em
        cima do corredor; se o ponto previsto for parede, encurta a projeção)."""
        if predict_s <= 0:
            return pos
        dx, dy = self._vel[0] * predict_s, self._vel[1] * predict_s
        n = math.hypot(dx, dy)
        if n > MAX_PREDICT_PX:
            dx, dy = dx * MAX_PREDICT_PX / n, dy * MAX_PREDICT_PX / n
        hgt, wid = self.walk.shape
        for f in (1.0, 0.5, 0.25):
            px, py = pos[0] + dx * f, pos[1] + dy * f
            ix, iy = int(round(px)), int(round(py))
            if 0 <= iy < hgt and 0 <= ix < wid and self.walk[iy, ix]:
                return (px, py)
        return pos

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
    def decide(self, pos, now: float | None = None, predict_s: float = 0.0) -> list[str]:
        """Teclas a pressionar agora. Se a seta não saiu do lugar dentro do tempo limite,
        passa para o próximo melhor comando (ex.: D -> D+S -> D+W ...).
        predict_s > 0: decide pela posição PREVISTA (a tecla só faz efeito daqui a pouco). Com 0 o resultado é
        o de sempre."""
        now = time.time() if now is None else now
        if self._last_pos is None or math.hypot(pos[0] - self._last_pos[0],
                                                pos[1] - self._last_pos[1]) >= self.min_move_px:
            self._last_pos, self._last_move_t, self.stuck_level = pos, now, 0
        elif now - self._last_move_t > self.stuck_timeout_s:
            self.stuck_level += 1
            self._last_move_t = now
        self._update_velocity(pos, now)
        ppos = self.predict(pos, predict_s)
        cmds = self.ranked_commands(ppos, self.target_for(ppos))
        return cmds[self.stuck_level % len(cmds)]

    def notify_paused(self, now: float | None = None) -> None:
        """Chame enquanto a seta está parada de propósito, para não contar como travamento."""
        self._last_move_t = time.time() if now is None else now
        self._vel, self._vel_buf = (0.0, 0.0), deque()   # parada de propósito: velocidade zerada
