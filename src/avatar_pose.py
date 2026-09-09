"""
Poses do avatar (posição + rotação) e timeline de troca de pose DENTRO do
mesmo vídeo.

Antes, o avatar ficava ancorado no rodapé, em left/center/right, do início
ao fim do vídeo (uma posição sorteada por vídeo -- ver avatar_manager.py).
Aqui o avatar CICLA determinístico pela lista `config.avatar_pose.pose_names`
(padrão: direita/esquerda, sem rotação -- "posição original", só alternando
de lado), trocando em CORTE SECO a cada `change_interval_seconds` (padrão
5s): uma hora está numa pose, no corte já aparece na outra, de uma vez, sem
transição suave. Não é sorteado -- é sempre a mesma sequência, sempre
começando pela primeira pose da lista.

O registro POSES também tem variantes com ângulo diagonal moderado
(~±22-34°), disponíveis se `pose_names` for reconfigurado pra incluí-las --
ver a ressalva abaixo sobre por que o ângulo é limitado.

Ângulo máximo limitado a ~34°
-------------------------------
  Poses giradas ~90° ("de lado") NÃO estão no registro: como o `rotate` do
  FFmpeg gira em torno do centro geométrico do frame inteiro (não da cabeça
  especificamente), e a cabeça do avatar fica na metade de cima do frame
  (não no centro), girar perto de 90° empurra a cabeça pra fora da área
  visível/cobre ela com o corpo -- confirmado visualmente e reportado como
  bug. Mantendo o ângulo em ~±34° a cabeça continua sempre visível.

Como funciona no FFmpeg
------------------------
  1. `rotate` gira o avatar (já com chroma key aplicado) num canvas
     quadrado fixo (`canvas_size`, a diagonal do avatar) grande o bastante
     para nunca cortar o conteúdo em nenhum ângulo -- o filtro `rotate`
     exige dimensões de saída CONSTANTES, então não dá pra variar esse
     canvas por quadro, só o ângulo dentro dele.
  2. `overlay` posiciona esse canvas quadrado sobre o fundo, com x/y
     variando no tempo (`t`, em segundos -- variável nativa do overlay)
     para que o CENTRO do avatar siga a pose ativa a cada instante.
  3. A troca de pose é um DEGRAU (função `gte(t, corte)`), não uma rampa --
     com `transition_min/max_seconds=0` (padrão), a posição/ângulo mudam
     instantaneamente no corte. Configurar esses campos > 0 no config.json
     volta a suavizar a transição, se um dia quiser esse efeito.
"""

import logging
import math
from dataclasses import dataclass, replace
from typing import Dict, List, Optional, Tuple

log = logging.getLogger("avatar-pose")


@dataclass
class Pose:
    name: str
    angle_deg: float      # rotação (graus, sentido horário). 0=normal, 180=cabeça p/ baixo
    cx_frac: float        # centro do avatar, fração da largura de saída (0..1)
    cy_frac: float        # centro do avatar, fração da altura de saída (0..1)
    scale_mult: float = 1.0  # ajuste fino de tamanho para essa pose (poses giradas ocupam mais área vertical)


# Registro de poses "âncora". cx/cy descrevem o CENTRO (não o canto) porque
# a rotação do FFmpeg acontece em torno do centro do avatar. Ângulo máximo
# ~34° -- acima disso a cabeça começa a sair da área visível (ver docstring
# do módulo).
POSES: Dict[str, Pose] = {
    "bottom_right":       Pose("bottom_right",       angle_deg=0,   cx_frac=0.75, cy_frac=0.70),
    "bottom_left":        Pose("bottom_left",        angle_deg=0,   cx_frac=0.25, cy_frac=0.70),
    "bottom_center":      Pose("bottom_center",      angle_deg=0,   cx_frac=0.50, cy_frac=0.70),
    "diagonal_right":     Pose("diagonal_right",     angle_deg=34,  cx_frac=0.70, cy_frac=0.32, scale_mult=0.92),
    "diagonal_left":      Pose("diagonal_left",      angle_deg=-34, cx_frac=0.30, cy_frac=0.32, scale_mult=0.92),
    "diagonal_right_mid": Pose("diagonal_right_mid", angle_deg=22,  cx_frac=0.68, cy_frac=0.52, scale_mult=0.94),
    "diagonal_left_mid":  Pose("diagonal_left_mid",  angle_deg=-22, cx_frac=0.32, cy_frac=0.52, scale_mult=0.94),
}

DEFAULT_POSE_NAMES = list(POSES.keys())


@dataclass
class PoseKeyframe:
    start_time: float
    pose: Pose


@dataclass
class AvatarOverlayExpr:
    """Expressões FFmpeg prontas para o filter_complex do avatar."""
    needs_rotation: bool     # False == nenhuma pose da timeline usa ângulo != 0 (pula o filtro `rotate`)
    rotate_angle_expr: str   # radianos, expressão em função de `t` (só usado se needs_rotation)
    overlay_x_expr: str      # pixels, expressão em função de `t`
    overlay_y_expr: str      # pixels, expressão em função de `t`
    canvas_w: int            # largura do canvas de overlay (== av_w se needs_rotation=False)
    canvas_h: int            # altura do canvas de overlay (== av_h se needs_rotation=False)
    scale_mult: float        # multiplicador de tamanho a aplicar no avatar (fixo p/ o vídeo inteiro)
    pose_sequence: List[str] # nomes das poses usadas, na ordem -- só para logging


def build_pose_timeline(
    duration: float,
    pose_cfg,
) -> Tuple[List[PoseKeyframe], float]:
    """
    Monta a sequência de poses do vídeo inteiro. Retorna (keyframes,
    transition_seconds).

    Alternância DETERMINÍSTICA (não sorteada): cicla por `pose_cfg.pose_names`
    em ordem, um corte a cada `change_interval_seconds` EXATOS (sem jitter)
    -- ex.: pose_names=["bottom_right","bottom_left"] com intervalo de 5s
    dá direita(0s) -> esquerda(5s) -> direita(10s) -> esquerda(15s) ...
    até o fim do vídeo. Sempre começa pelo primeiro nome da lista.

    Se `pose_cfg.enabled` for False, ou a lista tiver só 1 pose, ou o vídeo
    for curto demais (< change_interval_seconds), retorna uma pose fixa (a
    primeira da lista) do início ao fim.
    """
    available = [p for p in (pose_cfg.pose_names or DEFAULT_POSE_NAMES) if p in POSES] or DEFAULT_POSE_NAMES

    if not pose_cfg.enabled or len(available) < 2 or duration < pose_cfg.change_interval_seconds:
        return [PoseKeyframe(0.0, POSES[available[0]])], 0.0

    interval = max(pose_cfg.change_interval_seconds, pose_cfg.min_segment_seconds)
    n_segments = max(1, math.ceil(duration / interval))

    keyframes = [
        PoseKeyframe(i * interval, POSES[available[i % len(available)]])
        for i in range(n_segments)
        if i * interval < duration
    ]
    return keyframes, 0.0


def pose_scale_mult(keyframes: List[PoseKeyframe]) -> float:
    """
    Multiplicador de tamanho a aplicar nas DIMENSÕES do avatar (pixels),
    antes de escalar/rotacionar -- calculado ANTES de build_avatar_overlay
    porque av_w/av_h (que build_avatar_overlay recebe prontos) já precisam
    vir com esse fator aplicado. Usa o mínimo entre as poses da timeline
    para nenhuma pose (ex.: side_right, mais estreita) ficar sangrando
    demais da tela.
    """
    return min(kf.pose.scale_mult for kf in keyframes)


def clamp_poses_above_region(
    keyframes: List[PoseKeyframe],
    av_h: int,
    out_h: int,
    avoid_top_px: float,
    avoid_bottom_px: float,
    margin_px: float = 24.0,
) -> List[PoseKeyframe]:
    """
    Empurra pra CIMA (reduz cy_frac) qualquer pose cujo retângulo vertical
    [cy - av_h/2, cy + av_h/2] invada a faixa [avoid_top_px, avoid_bottom_px]
    (+ margem) -- usada pra manter o avatar sempre acima do título do
    produto (localizado via OCR e projetado pra tela de saída, ver
    project_box_to_screen() em src/price_badge.py), sem tampá-lo. Poses que
    já ficam fora da faixa (acima OU abaixo, com folga) não são tocadas.

    Se o avatar for alto demais pra caber inteiro acima da faixa sem sair
    do topo do quadro, usa a posição mais alta possível (cy = av_h/2 +
    margem) e loga um aviso -- melhor sobrepor um pouco do que cortar o
    avatar pra fora da tela.
    """
    limit_cy = avoid_top_px - margin_px - av_h / 2.0
    min_cy = av_h / 2.0 + margin_px
    clamped_limit_cy = max(limit_cy, min_cy)

    adjusted: List[PoseKeyframe] = []
    moved_any = False
    limit_too_tight = False
    for kf in keyframes:
        cy_px = kf.pose.cy_frac * out_h
        avatar_top = cy_px - av_h / 2.0
        avatar_bottom = cy_px + av_h / 2.0
        overlaps = avatar_bottom > avoid_top_px - margin_px and avatar_top < avoid_bottom_px + margin_px

        if overlaps:
            moved_any = True
            limit_too_tight = limit_too_tight or limit_cy < min_cy
            adjusted.append(PoseKeyframe(kf.start_time, replace(kf.pose, cy_frac=clamped_limit_cy / out_h)))
        else:
            adjusted.append(kf)

    if limit_too_tight:
        log.warning(
            "  [avatar-pose] Avatar (%dpx de altura) não cabe inteiro acima "
            "da faixa do título do produto (topo projetado em %.0fpx) sem "
            "sair do quadro -- usando a posição mais alta possível (pode "
            "sobrepor levemente).",
            av_h, avoid_top_px,
        )
    if moved_any:
        log.info(
            "  [avatar-pose] Avatar reposicionado pra não tampar o título do "
            "produto (faixa projetada: %.0f-%.0fpx de %dpx).",
            avoid_top_px, avoid_bottom_px, out_h,
        )

    return adjusted


def _lerp_expr(values: List[float], times: List[float], transition: float) -> str:
    """
    Expressão FFmpeg por trechos: começa em `values[0]` e, para cada corte
    em `times[i]`, soma a diferença para `values[i]`.

    `transition <= 0` (padrão) -- CORTE SECO: usa `gte(t,corte)` (função
    degrau do FFmpeg, 1 se t>=corte senão 0) -- o valor muda instantaneamente
    no corte, sem rampa.
    `transition > 0` -- rampa linear de `transition` segundos ao redor do
    corte, pra quem preferir uma transição suave.

    Funciona com qualquer filtro cuja variável de tempo se chame `t`
    (overlay, rotate).
    """
    if len(values) == 1:
        return f"{values[0]:.4f}"

    expr = f"{values[0]:.4f}"
    for i in range(1, len(values)):
        delta = values[i] - values[i - 1]
        if abs(delta) < 1e-6:
            continue
        if transition <= 0:
            progress = f"gte(t,{times[i]:.3f})"
        else:
            progress = f"min(1,max(0,(t-{times[i]:.3f})/{transition:.4f}))"
        expr = f"(({expr})+({delta:.4f})*({progress}))"
    return expr


def build_avatar_overlay(
    keyframes: List[PoseKeyframe],
    transition: float,
    av_w: int,
    av_h: int,
    out_w: int,
    out_h: int,
) -> AvatarOverlayExpr:
    """
    Constrói as expressões de ângulo (rotate) e posição (overlay x/y) a
    partir da timeline de poses, e o tamanho do canvas quadrado necessário
    para a rotação nunca cortar o avatar em nenhum ângulo.
    """
    times = [kf.start_time for kf in keyframes]
    angles = [kf.pose.angle_deg for kf in keyframes]
    cxs = [kf.pose.cx_frac * out_w for kf in keyframes]
    cys = [kf.pose.cy_frac * out_h for kf in keyframes]

    cx_expr = _lerp_expr(cxs, times, transition)
    cy_expr = _lerp_expr(cys, times, transition)
    scale_mult = min(kf.pose.scale_mult for kf in keyframes)
    needs_rotation = any(abs(a) > 0.01 for a in angles)

    if needs_rotation:
        # `rotate` exige dimensões de saída constantes -- usa um canvas
        # quadrado do tamanho da diagonal do avatar, grande o bastante para
        # nunca cortar o conteúdo em nenhum ângulo.
        diag = int(math.ceil(math.hypot(av_w, av_h)))
        diag += diag % 2  # dimensão par (H.264)
        angle_expr_deg = _lerp_expr(angles, times, transition)
        return AvatarOverlayExpr(
            needs_rotation=True,
            rotate_angle_expr=f"(({angle_expr_deg})*PI/180)",
            overlay_x_expr=f"({cx_expr})-{diag}/2",
            overlay_y_expr=f"({cy_expr})-{diag}/2",
            canvas_w=diag,
            canvas_h=diag,
            scale_mult=scale_mult,
            pose_sequence=[kf.pose.name for kf in keyframes],
        )

    # Nenhuma pose da timeline gira o avatar -- pula o filtro `rotate`
    # (evita reamostragem/custo desnecessário) e usa o retângulo av_w x av_h
    # direto como canvas de overlay.
    return AvatarOverlayExpr(
        needs_rotation=False,
        rotate_angle_expr="0",
        overlay_x_expr=f"({cx_expr})-{av_w}/2",
        overlay_y_expr=f"({cy_expr})-{av_h}/2",
        canvas_w=av_w,
        canvas_h=av_h,
        scale_mult=scale_mult,
        pose_sequence=[kf.pose.name for kf in keyframes],
    )
