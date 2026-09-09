"""
Núcleo de processamento de vídeo — layout Produto (fundo) + Avatar (frente).

Layout do vídeo gerado:
  - Produto: a print preenche o quadro 1080x1920 inteiro (modo "cover" --
    igual ao CapCut quando você estica um clipe pra cobrir o quadro todo:
    o excedente é cortado nas bordas, nunca sobra preto), respirando (zoom
    aumenta e diminui, sempre cobrindo a tela -- pré-renderizado à parte,
    ver ken_burns.py), com o GIF do cadeado por cima do preço bloqueado se
    aplicável (ver price_badge.py).
  - Avatar: reduzido, com chroma key (fundo verde removido), sobreposto por
    cima de tudo. Posição e rotação seguem uma timeline de "poses" que pode
    trocar no meio do vídeo (ver avatar_pose.py) -- SEMPRE relativa ao
    quadro 1080x1920 inteiro.

Entradas FFmpeg (índices calculados dinamicamente -- ver _build_ffmpeg_cmd):
  [0] Vídeo do avatar (com fundo verde, repetido via stream_loop)
  [1] Imagem do produto (usada direto só no modo estático -- ver ken_burns.py)
  [ ] (se animação ativa) Ciclo de respiração pré-renderizado, repetido via
      -stream_loop -1 (mesma técnica do GIF do cadeado)
  [ ] (opcional) GIF do cadeado, repetido via -stream_loop -1

Filter complex (single-pass):
  [bg_panel] produto (estático: [1:v] scale+crop; animado: clip pré-pronto) → quadro inteiro, respirando
  color=black + [bg_panel] overlay(0,0)        → [bg]       base + produto (cobre 100% do quadro)
  [badge:v] format=rgba + scale                → [badge]     cadeado animado (opcional)
  [bg][badge] overlay(x,y)                     → [bg2]       fundo + badge do preço
  [0:v] chromakey + scale (+ rotate opcional)  → [av]        avatar com alpha, pose(t)
  [bg2][av] overlay(x(t),y(t),format=auto)     → [out]       composição final
"""

import logging
import random
import shutil
from pathlib import Path
from typing import Optional, Tuple

from .avatar_manager import compute_avatar_dimensions
from .avatar_pose import build_avatar_overlay, build_pose_timeline, clamp_poses_above_region, pose_scale_mult
from .chroma_key import build_chromakey_filter
from .config_loader import AppConfig
from .ffmpeg_utils import get_video_dimensions, run_ffmpeg
from .ken_burns import build_ken_burns
from .price_badge import build_price_badge_overlay, project_box_to_screen
from .price_blocker import PriceLockResult

logger = logging.getLogger(__name__)


def process_video(
    background_path: str,
    avatar_path: str,
    output_path: str,
    duration: float,
    config: AppConfig,
    ffmpeg_path: str,
    ffprobe_path: str,
    price_lock_result: Optional[PriceLockResult] = None,
    assets_dir: Optional[Path] = None,
    title_band: Optional[Tuple[float, float, float, float]] = None,
) -> None:
    """
    Gera um vídeo TikTok Shop:
      - Imagem do produto preenchendo o quadro inteiro (modo "cover"),
        respirando (zoom aumenta e diminui, sempre cobrindo a tela -- ver
        ken_burns.py), com o GIF do cadeado sobre a barra de preço
        bloqueado se `price_lock_result` for passado (ver price_badge.py).
      - Avatar (fundo verde removido via chroma key) reduzido, seguindo uma
        timeline de poses (posição + rotação) que pode trocar no meio do
        vídeo (config.avatar_pose) -- reposicionado pra ficar ACIMA do
        título do produto se `title_band` for passado (caixa x1,y1,x2,y2 em
        pixels da imagem ORIGINAL de fundo, vinda de
        ProductNamer.identify_with_band(), ver src/product_namer.py), pra
        não tampar o nome do produto que já está "assado" na print.
    """
    cmd, temp_dir = _build_ffmpeg_cmd(
        background_path=background_path,
        avatar_path=avatar_path,
        output_path=output_path,
        duration=duration,
        config=config,
        ffmpeg_path=ffmpeg_path,
        ffprobe_path=ffprobe_path,
        price_lock_result=price_lock_result,
        assets_dir=assets_dir,
        title_band=title_band,
    )
    try:
        run_ffmpeg(cmd, description=f"renderizar {output_path}")
    finally:
        if temp_dir:
            shutil.rmtree(temp_dir, ignore_errors=True)


def _build_ffmpeg_cmd(
    background_path: str,
    avatar_path: str,
    output_path: str,
    duration: float,
    config: AppConfig,
    ffmpeg_path: str,
    ffprobe_path: str,
    price_lock_result: Optional[PriceLockResult] = None,
    assets_dir: Optional[Path] = None,
    title_band: Optional[Tuple[float, float, float, float]] = None,
) -> "tuple[list, Optional[str]]":
    """
    Constrói o comando FFmpeg para o layout Produto (fundo animado) + Avatar (frente).
    Retorna (cmd, temp_dir) -- temp_dir (se houver) precisa ser removido pelo
    chamador depois que o render terminar (ver process_video).
    """
    out_w     = config.resolution.width    # 1080
    out_h     = config.resolution.height   # 1920
    fps       = config.fps
    ck        = config.chroma_key
    loops     = max(1, config.video_loops)
    total_dur = duration * loops

    # -- Painel do produto: ocupa o quadro 1080x1920 inteiro -- a print do
    # produto preenche a tela toda (modo "cover", ver ken_burns.py), sem
    # sobrar fundo preto nas bordas.
    panel_w = out_w
    panel_h = out_h
    panel_offset_x = 0
    panel_offset_y = 0

    from PIL import Image
    orig_w, orig_h = Image.open(background_path).size

    # Animação de respiração (zoom aumenta e diminui, sempre cobrindo a
    # tela -- pré-renderizada à parte, ver ken_burns.py) do produto
    kb = build_ken_burns(
        config, total_dur, orig_w, orig_h, panel_w, panel_h,
        background_path, ffmpeg_path,
    )
    fit_scale = kb.fw / orig_w  # == kb.fh/orig_h (mesma proporção, "cover")

    # Badge (GIF do cadeado) posicionado a partir da projeção da caixa do
    # preço (calculada por price_blocker.py) para a tela de saída em t=0
    # da respiração que acabou de ser gerada -- ver price_badge.py.
    price_badge = None
    if price_lock_result is not None and assets_dir is not None:
        price_badge = build_price_badge_overlay(
            price_lock_result, config, kb, assets_dir,
            fit_scale, panel_offset_x, panel_offset_y,
        )

    # Chroma key do avatar (retorna: "format=yuva420p,chromakey=...")
    ck_chain = build_chromakey_filter(ck)

    # -- Timeline de poses do avatar (posição + rotação, pode trocar no --
    # meio do vídeo -- ver avatar_pose.py) e dimensões finais do avatar.
    avatar_orig_w, avatar_orig_h = get_video_dimensions(avatar_path, ffprobe_path)
    base_scale = random.choice(config.avatar.scales)
    keyframes, transition = build_pose_timeline(total_dur, config.avatar_pose)
    effective_scale = base_scale * pose_scale_mult(keyframes)
    av_w, av_h = compute_avatar_dimensions(avatar_orig_w, avatar_orig_h, config, effective_scale)

    # -- Mantém o avatar ACIMA do título do produto (já "assado" na print --
    # não desenhamos texto nenhum) -- projeta a caixa do título (OCR, ver
    # ProductNamer.identify_with_band()) pra tela de saída com a mesma
    # matemática "cover" + respiração usada pro badge do cadeado (ver
    # project_box_to_screen() em price_badge.py) e empurra pra cima qualquer
    # pose que invadiria essa faixa.
    if title_band is not None:
        projected_title = project_box_to_screen(
            title_band, (orig_w, orig_h), config, kb,
            fit_scale, panel_offset_x, panel_offset_y,
        )
        if projected_title is not None:
            _, title_y, _, title_h = projected_title
            keyframes = clamp_poses_above_region(
                keyframes, av_h, out_h,
                avoid_top_px=title_y, avoid_bottom_px=title_y + title_h,
            )

    pose = build_avatar_overlay(keyframes, transition, av_w, av_h, out_w, out_h)

    logger.info(
        "[AVATAR-POSE] sequência=%s  transição=%.2fs  rotação=%s",
        pose.pose_sequence, transition, pose.needs_rotation,
    )

    # -- Inputs: avatar (0) e imagem do produto (1) sempre presentes; a --
    # partir daí, os índices dependem de quais opcionais entram (clipe de
    # respiração pré-renderizado, GIF do cadeado).
    cmd = [
        ffmpeg_path,
        "-y",
        "-stream_loop", str(loops - 1),
        "-i", avatar_path,               # Input 0: avatar (mp4 com fundo verde)
        "-loop", "1",
        "-framerate", str(fps),
        "-i", background_path,           # Input 1: imagem do produto
    ]
    next_idx = 2

    if kb.clip_path is not None:
        breathing_idx = next_idx
        next_idx += 1
        cmd += ["-stream_loop", "-1", "-i", kb.clip_path]  # ciclo de respiração pré-renderizado
    else:
        breathing_idx = None

    if price_badge is not None:
        badge_idx = next_idx
        next_idx += 1
        cmd += ["-stream_loop", "-1", "-i", price_badge.gif_path]  # GIF do cadeado
    else:
        badge_idx = None

    # [bg_panel]: produto respirando, já do tamanho do painel.
    if breathing_idx is not None:
        filters = [f"[{breathing_idx}:v]fps={fps},setpts=PTS-STARTPTS[bg_panel]"]
    else:
        filters = list(kb.filters)  # modo estático -- lê [1:v] diretamente

    # [av]: avatar com fundo verde removido, escalado para av_w x av_h, com
    # rotação animada opcional (só entra no filtro se alguma pose da
    # timeline girar o avatar -- ver AvatarOverlayExpr.needs_rotation).
    av_chain = f"[0:v]{ck_chain},scale={av_w}:{av_h},format=rgba"
    if pose.needs_rotation:
        av_chain += (
            f",rotate=angle='{pose.rotate_angle_expr}'"
            f":ow={pose.canvas_w}:oh={pose.canvas_h}:fillcolor=black@0.0"
        )
    av_filter = f"{av_chain},format=yuva420p[av]"

    filters += [
        f"color=c=black:s={out_w}x{out_h}:r={fps}:d={total_dur + 1:.3f}[blackbg]",
        f"[blackbg][bg_panel]overlay={panel_offset_x}:{panel_offset_y}[bg]",
        av_filter,
    ]
    bg_label = "[bg]"

    # [badge]: GIF do cadeado sobre a barra de preço bloqueado (opcional)
    if price_badge is not None:
        filters.append(
            f"[{badge_idx}:v]format=rgba,scale={price_badge.w}:{price_badge.h}[badge]"
        )
        filters.append(
            f"{bg_label}[badge]overlay={price_badge.x}:{price_badge.y}:format=auto[bg2]"
        )
        bg_label = "[bg2]"

    # Composição final: fundo (+ badge) + avatar, posicionado em pose.overlay_x/y(t)
    filters.append(
        f"{bg_label}[av]overlay=x='{pose.overlay_x_expr}':y='{pose.overlay_y_expr}':format=auto[out]"
    )

    filter_complex = ";".join(filters)

    cmd += [
        "-filter_complex", filter_complex,
        "-map", "[out]",
        "-map", "0:a?",                  # áudio do avatar (opcional)
        "-c:v", config.codec,
        "-preset", config.preset,
        "-crf", str(config.crf),
        "-r", str(fps),
        "-pix_fmt", "yuv420p",
        "-t", str(total_dur),
        "-movflags", "+faststart",
        output_path,
    ]
    return cmd, kb.temp_dir
