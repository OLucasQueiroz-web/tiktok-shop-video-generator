"""
Gerencia a seleção rotativa de avatares e o cálculo de dimensões/posicionamento.
"""

import logging
from typing import List, Tuple

from .config_loader import AppConfig

logger = logging.getLogger(__name__)


class AvatarSelector:
    """
    Seleciona avatares em ordem circular.
    Produto 1 -> avatar1, Produto 2 -> avatar2, ..., Produto N+1 -> avatar1, etc.
    """

    def __init__(self, avatar_paths: List[str]):
        if not avatar_paths:
            raise ValueError("Lista de avatares está vazia")
        self._paths = avatar_paths
        self._index = 0

    def next(self) -> str:
        path = self._paths[self._index % len(self._paths)]
        self._index += 1
        return path

    @property
    def count(self) -> int:
        return len(self._paths)


def compute_avatar_dimensions(
    orig_w: int,
    orig_h: int,
    config: AppConfig,
    scale_factor: float,
) -> Tuple[int, int]:
    """
    Calcula largura e altura alvo do avatar para o overlay.

    Lógica:
    - A largura base é uma fração (base_width_ratio) da largura do vídeo de saída.
    - scale_factor aplica variação adicional (+/- %).
    - A altura preserva a proporção original do avatar.
    - Dimensões são arredondadas para múltiplos de 2 (exigido pelo H.264).
    """
    if orig_w <= 0 or orig_h <= 0:
        raise ValueError(f"Dimensões inválidas do avatar: {orig_w}x{orig_h}")

    out_w = config.resolution.width
    out_h = config.resolution.height

    target_w = int(out_w * config.avatar.base_width_ratio * scale_factor)
    aspect_ratio = orig_h / orig_w
    target_h = int(target_w * aspect_ratio)

    # Limita para não exceder a resolução de saída
    if target_w > out_w:
        target_w = out_w
        target_h = int(target_w * aspect_ratio)
    if target_h > out_h:
        target_h = out_h
        target_w = int(target_h / aspect_ratio)

    # H.264 exige dimensões pares
    target_w = target_w if target_w % 2 == 0 else target_w - 1
    target_h = target_h if target_h % 2 == 0 else target_h - 1

    return max(target_w, 2), max(target_h, 2)
