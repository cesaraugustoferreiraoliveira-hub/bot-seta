"""Velocidade do bot conforme as sprites na tela + envio de teclas sem 'buracos' entre os passos.

Sem GUI e sem Windows: recebe a contagem de sprites e a config e devolve um perfil; o KeyHolder só chama as
funções de apertar/soltar que você passar (testável com funções falsas).

Faixas (definidas na UI):  contagem <= vel_muito_rapido_ate  -> muito rápido
                           contagem <= vel_rapido_ate        -> rápido
                           acima                             -> devagar
Cada faixa tem:
  passo (ms)     quanto as teclas ficam pressionadas por ciclo antes de soltar. 0 = CONTÍNUO: não solta entre os
                 ciclos, só troca as teclas quando a direção muda (a seta não para entre um passo e outro).
  previsão (ms)  quanto à frente a posição da seta é projetada (pela velocidade medida) antes de decidir a tecla,
                 para já virar na hora certa em vez de reagir só depois que a seta passou do ponto.
"""
from __future__ import annotations
from dataclasses import dataclass

from .config import DEFAULTS

OPOSTAS = {"w": "s", "s": "w", "a": "d", "d": "a"}
NOMES = ("muito rápido", "rápido", "devagar")


@dataclass(frozen=True)
class SpeedProfile:
    nome: str
    passo_s: float          # 0 = contínuo
    previsao_s: float

    @property
    def continuo(self) -> bool:
        return self.passo_s <= 0


def _num(cfg, key: str) -> float:
    try:
        return float(cfg.get(key, DEFAULTS[key]))
    except (TypeError, ValueError):
        return float(DEFAULTS[key])


def legacy_profile(cfg) -> SpeedProfile:
    """Comportamento de antes: segura o 'passo_ms', solta, sem previsão."""
    return SpeedProfile("padrão", max(0.0, _num(cfg, "passo_ms")) / 1000.0, 0.0)


def profile_for(count: int | None, cfg) -> SpeedProfile:
    """Perfil de velocidade para `count` sprites na tela. count=None (detecção de sprites desligada): faixa 'rápido'."""
    if not cfg.get("vel_ativo", DEFAULTS["vel_ativo"]):
        return legacy_profile(cfg)
    if count is None:
        nome = "rápido"
    elif count <= _num(cfg, "vel_muito_rapido_ate"):
        nome = "muito rápido"
    elif count <= _num(cfg, "vel_rapido_ate"):
        nome = "rápido"
    else:
        nome = "devagar"
    chave = {"muito rápido": "muito_rapido", "rápido": "rapido", "devagar": "devagar"}[nome]
    return SpeedProfile(nome, max(0.0, _num(cfg, f"vel_{chave}_passo_ms")) / 1000.0,
                        max(0.0, _num(cfg, f"vel_{chave}_previsao_ms")) / 1000.0)


class KeyHolder:
    """Mantém as teclas de movimento pressionadas e só mexe no que mudou.

    Ao virar (ex.: D -> D+S, ou D -> S) a tecla nova desce ANTES da antiga subir, então não há instante sem
    tecla. Teclas opostas (A x D, W x S) nunca ficam juntas: a antiga sobe antes da nova descer."""

    def __init__(self, down, up):
        self._down, self._up = down, up
        self.held: set[str] = set()

    def apply(self, keys) -> None:
        new = set(keys)
        for k in sorted(k for k in self.held - new if OPOSTAS.get(k) in new):
            self._up(k)
            self.held.discard(k)
        for k in sorted(new - self.held):
            self._down(k)
            self.held.add(k)
        for k in sorted(self.held - new):
            self._up(k)
            self.held.discard(k)

    def release(self) -> None:
        for k in sorted(self.held):
            self._up(k)
        self.held.clear()

    def forget(self) -> None:
        """Alguém já soltou tudo por fora (release_all): só esquece o estado, sem enviar nada."""
        self.held.clear()
