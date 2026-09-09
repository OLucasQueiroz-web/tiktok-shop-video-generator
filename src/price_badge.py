"""
Badge animado (GIF) do cadeado que fica por cima da barra de preço bloqueado.

Duas responsabilidades:
  1. ensure_badge_gif()       -- gera (e cacheia em disco) um GIF de um
                                  cadeado "pulsando" com fundo transparente.
  2. project_box_to_screen()  -- projeta a caixa do preço (calculada por
                                  price_blocker.py em coordenadas da imagem
                                  ORIGINAL) para a posição/tamanho que ela
                                  ocupa na tela de saída (1080x1920) no
                                  instante t=0 da respiração (zoompan),
                                  aplicando a mesma matemática de escala
                                  "contain" + canvas grande + zoompan usada
                                  em video_processor.py/ken_burns.py.

Por que projetar em vez de sincronizar quadro a quadro
--------------------------------------------------------
  O fundo (produto) nunca fica parado -- zoompan pulsa zoom e deriva
  x/y continuamente (ver ken_burns.py). Reproduzir isso quadro a quadro num
  filtro overlay separado duplicaria as expressões z/x/y do zoompan dentro
  de outro filtro -- caro e frágil (motivo pelo qual o projeto historicamente
  evitou overlay de GIF, ver price_blocker.py).
  Em vez disso, o GIF fica numa posição FIXA na tela, calculada a partir do
  estado do zoompan em t=0, com uma margem de segurança proporcional ao
  tamanho da própria caixa (não um valor fixo -- ver project_box_to_screen)
  que cobre a leve deriva que o zoom da respiração pode causar ao longo do
  vídeo. Como a pixelização do preço já é assada direto nos pixels da
  imagem (ver price_blocker.py), o cadeado é só decorativo -- não faz mal
  nenhum se ele não cobrir a barra com perfeição no pico do zoom; o que
  importa é não vazar pra cima de outro texto quando a caixa é curta.
"""

import logging
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple

log = logging.getLogger("price-badge")

BBox = Tuple[float, float, float, float]


@dataclass
class PriceBadgeOverlay:
    """Tudo que video_processor.py precisa para sobrepor o GIF do cadeado."""
    gif_path: str
    x: int
    y: int
    w: int
    h: int


def _hex_to_rgba(hex_color: str, alpha: int = 255) -> Tuple[int, int, int, int]:
    h = hex_color.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return r, g, b, alpha


def _draw_padlock(canvas_size: int, color: Tuple[int, int, int, int], scale: float = 1.0):
    """Desenha um cadeado vetorial (PIL) num canvas quadrado transparente."""
    from PIL import Image, ImageDraw

    im = Image.new("RGBA", (canvas_size, canvas_size), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)

    cx, cy = canvas_size / 2, canvas_size / 2
    s = canvas_size * 0.62 * scale

    body_w, body_h = s, s * 0.72
    body_top = cy - body_h * 0.12
    body_bottom = body_top + body_h
    body_left, body_right = cx - body_w / 2, cx + body_w / 2

    shackle_w, shackle_h = body_w * 0.62, body_h * 0.95
    shackle_left, shackle_right = cx - shackle_w / 2, cx + shackle_w / 2
    shackle_top = body_top - shackle_h * 0.72
    line_w = max(2, int(canvas_size * 0.045 * scale))

    d.arc(
        [shackle_left, shackle_top, shackle_right, shackle_top + shackle_h],
        start=180, end=360, fill=color, width=line_w,
    )
    d.line([shackle_left, shackle_top + shackle_h * 0.5, shackle_left, body_top + line_w],
           fill=color, width=line_w)
    d.line([shackle_right, shackle_top + shackle_h * 0.5, shackle_right, body_top + line_w],
           fill=color, width=line_w)

    radius = body_w * 0.14
    d.rounded_rectangle([body_left, body_top, body_right, body_bottom], radius=radius, fill=color)

    keyhole_r = body_w * 0.09
    khx, khy = cx, body_top + body_h * 0.42
    d.ellipse([khx - keyhole_r, khy - keyhole_r, khx + keyhole_r, khy + keyhole_r],
               fill=(40, 30, 10, 255))
    d.polygon(
        [
            (khx - keyhole_r * 0.55, khy + keyhole_r * 0.3),
            (khx + keyhole_r * 0.55, khy + keyhole_r * 0.3),
            (khx + keyhole_r * 0.28, khy + keyhole_r * 1.9),
            (khx - keyhole_r * 0.28, khy + keyhole_r * 1.9),
        ],
        fill=(40, 30, 10, 255),
    )
    return im


def _render_pulse_frame(size: int, color: Tuple[int, int, int, int], t: float):
    """
    Um quadro da animação: cadeado supersampleado, com leve pulsação de
    escala + oscilação de rotação (efeito "chamando atenção"), fundo 100%
    transparente.
    """
    from PIL import Image

    canvas = size * 2  # supersample p/ anti-aliasing
    scale = 1.0 + 0.10 * math.sin(t * 2 * math.pi)
    angle = 8.0 * math.sin(t * 2 * math.pi + math.pi / 2)

    lock = _draw_padlock(canvas, color, scale=scale)
    lock = lock.rotate(angle, resample=Image.BICUBIC, center=(canvas / 2, canvas / 2))
    return lock.resize((size, size), Image.LANCZOS)


def ensure_badge_gif(
    assets_dir: Path,
    lock_color: str = "#FFFFFF",
    size: int = 320,
    fps: int = 15,
    loop_seconds: float = 1.6,
) -> str:
    """
    Gera (se ainda não existir em cache) o GIF animado do cadeado e retorna
    o caminho do arquivo. O nome do arquivo já incorpora cor/tamanho, então
    mudar esses parâmetros no config gera um novo arquivo automaticamente em
    vez de reaproveitar um cache desatualizado.
    """
    from PIL import Image

    assets_dir.mkdir(parents=True, exist_ok=True)
    color_slug = lock_color.lstrip("#").upper()
    out_path = assets_dir / f"price_lock_badge_{color_slug}_{size}.gif"
    if out_path.is_file():
        return str(out_path)

    color = _hex_to_rgba(lock_color)
    n_frames = max(2, round(fps * loop_seconds))
    frame_ms = round(1000 / fps)

    gif_frames = []
    for i in range(n_frames):
        t = i / n_frames  # 0..1 (ciclo completo -> GIF looping sem "salto")
        frame_rgba = _render_pulse_frame(size, color, t)

        alpha = frame_rgba.split()[3]
        frame_rgb = frame_rgba.convert("RGB")
        frame_p = frame_rgb.convert("P", palette=Image.ADAPTIVE, colors=255)
        # índice 255 reservado como transparente -- pixels com alpha baixo
        # (fora do desenho do cadeado) viram esse índice.
        mask = Image.eval(alpha, lambda a: 255 if a <= 128 else 0)
        frame_p.paste(255, mask)
        gif_frames.append(frame_p)

    gif_frames[0].save(
        out_path,
        save_all=True,
        append_images=gif_frames[1:],
        duration=frame_ms,
        loop=0,
        disposal=2,
        transparency=255,
        optimize=False,
    )
    log.info("[price-badge] GIF do cadeado gerado -> %s (%d frames)", out_path.name, n_frames)
    return str(out_path)


def project_box_to_screen(
    box: BBox,
    orig_size: Tuple[int, int],
    config,
    kb,
    fit_scale: float,
    panel_offset_x: int,
    panel_offset_y: int,
) -> Optional[Tuple[int, int, int, int]]:
    """
    Projeta `box` (coordenadas da imagem ORIGINAL, já com padding -- ver
    PriceLockResult.box) para (x, y, w, h) em pixels da tela de saída,
    replicando a mesma escala uniforme "cover" (`fit_scale`, preenche o
    painel) + posição no canvas grande (`kb.pad_x/pad_y`) + zoompan (estado
    em t=0) + posição do painel na tela (`panel_offset_x/y`) do pipeline em
    video_processor.py. Adiciona uma margem de segurança para absorver a
    deriva do zoom ao longo do vídeo (ver docstring do módulo). Retorna
    None se a caixa projetada ficar degenerada/fora da tela.

    IMPORTANTE (modo "breathing", ver ken_burns.py): quando a imagem de
    origem é proporcionalmente mais alta que o painel de saída (canvas
    `big_w x big_h` != painel `out_w x out_h`), o zoompan real do FFmpeg
    recorta uma janela do tamanho `canvas/zoom` -- NÃO `painel/zoom` como a
    fórmula de x/y de ken_burns.py pretendia -- e prende essa janela dentro
    dos limites do canvas. Isso significa que em zoom baixo (perto de 1.0)
    a janela às vezes NÃO fica centralizada (fica encostada numa borda,
    porque a posição "pretendida" ultrapassaria o canvas) -- só passa a
    ficar centralizada a partir de um certo zoom. Ignorar isso (assumir
    sempre centralizado) foi o que causava o cadeado saindo da barra de
    preço em imagens desse formato. Replicamos aqui o mesmo recorte
    (tamanho + trava de borda) que o FFmpeg realmente aplica.
    """
    orig_w, orig_h = orig_size
    if orig_w <= 0 or orig_h <= 0:
        return None

    out_w = config.resolution.width
    out_h = config.resolution.height

    # escala "contain" uniforme (sem cortar) + posição dentro do canvas grande
    x1, y1, x2, y2 = box
    bx1 = x1 * fit_scale + kb.pad_x
    by1 = y1 * fit_scale + kb.pad_y
    bx2 = x2 * fit_scale + kb.pad_x
    by2 = y2 * fit_scale + kb.pad_y

    if kb.effect_type == "breathing":
        def _axis_at_z(p: float, canvas_size: float, panel_size: float, offset: float, z: float) -> float:
            window = canvas_size / z
            intended_pos = (canvas_size - panel_size / z) / 2.0
            pos = max(0.0, min(intended_pos, canvas_size - window))
            scale = panel_size / window
            return (p - pos) * scale + offset

        # O ciclo de respiração passa por TODO o intervalo de zoom [1.0,
        # zoom_ceiling] repetidamente -- e, pela trava de borda acima, a
        # posição da janela não varia linearmente com o zoom (fica presa
        # numa borda até um certo ponto, só depois passa a centralizar).
        # Como o badge fica numa posição FIXA o vídeo inteiro, em vez de
        # assumir uma margem fixa, amostramos vários pontos do intervalo de
        # zoom e usamos a UNIÃO das posições projetadas -- cobre o ciclo
        # inteiro exatamente, sem depender de uma margem arbitrária.
        n_samples = 24
        zs = [
            1.0 + (kb.zoom_ceiling - 1.0) * i / (n_samples - 1)
            for i in range(n_samples)
        ]

        xs1 = [_axis_at_z(bx1, kb.big_w, out_w, panel_offset_x, z) for z in zs]
        xs2 = [_axis_at_z(bx2, kb.big_w, out_w, panel_offset_x, z) for z in zs]
        ys1 = [_axis_at_z(by1, kb.big_h, out_h, panel_offset_y, z) for z in zs]
        ys2 = [_axis_at_z(by2, kb.big_h, out_h, panel_offset_y, z) for z in zs]

        ox1, ox2 = min(xs1 + xs2), max(xs1 + xs2)
        oy1, oy2 = min(ys1 + ys2), max(ys1 + ys2)
    else:
        # Modo estático: crop simples e centralizado (`crop=painel:painel`
        # em ken_burns.py), sem zoompan -- a fórmula original serve.
        ox1 = (bx1 - kb.x0) * kb.z0 + panel_offset_x
        oy1 = (by1 - kb.y0) * kb.z0 + panel_offset_y
        ox2 = (bx2 - kb.x0) * kb.z0 + panel_offset_x
        oy2 = (by2 - kb.y0) * kb.z0 + panel_offset_y

    # Pequena margem residual (antialiasing/arredondamento) -- a cobertura
    # do ciclo de zoom inteiro já vem da união acima no modo "breathing".
    drift_x = max(3.0, (ox2 - ox1) * 0.03)
    drift_y = max(3.0, (oy2 - oy1) * 0.03)
    ox1 -= drift_x
    ox2 += drift_x
    oy1 -= drift_y
    oy2 += drift_y

    ox1 = max(0.0, ox1)
    oy1 = max(0.0, oy1)
    ox2 = min(float(out_w), ox2)
    oy2 = min(float(out_h), oy2)

    w = ox2 - ox1
    h = oy2 - oy1
    if w < 16 or h < 16:
        return None

    x = int(ox1)
    y = int(oy1)
    wi = int(w) - (int(w) % 2)
    hi = int(h) - (int(h) % 2)
    if wi < 2 or hi < 2:
        return None

    return x, y, wi, hi


def build_price_badge_overlay(
    lock_result,
    config,
    kb,
    assets_dir: Path,
    fit_scale: float,
    panel_offset_x: int,
    panel_offset_y: int,
) -> Optional[PriceBadgeOverlay]:
    """
    Junta ensure_badge_gif() + project_box_to_screen() num único passo, a
    partir do PriceLockResult retornado por PriceBlocker.apply_lock().
    Retorna None se price_lock.badge_enabled=False, a projeção falhar, ou
    algo der errado gerando o GIF -- nesses casos o chamador simplesmente
    não sobrepõe o badge (a barra pixelizada assada na imagem continua
    cobrindo o preço normalmente).
    """
    if lock_result is None or not config.price_lock.badge_enabled:
        return None

    projected = project_box_to_screen(
        lock_result.box, lock_result.orig_size, config, kb,
        fit_scale, panel_offset_x, panel_offset_y,
    )
    if projected is None:
        log.info("[price-badge] Projeção da caixa de preço fora da tela -- badge não aplicado.")
        return None

    try:
        gif_path = ensure_badge_gif(
            assets_dir,
            lock_color=config.price_lock.lock_color,
            size=config.price_lock.badge_size,
            fps=config.price_lock.badge_fps,
            loop_seconds=config.price_lock.badge_loop_seconds,
        )
    except Exception as exc:
        log.warning("[price-badge] Falha ao gerar GIF do cadeado: %s", exc)
        return None

    x, y, w, h = projected
    return PriceBadgeOverlay(gif_path=gif_path, x=x, y=y, w=w, h=h)
