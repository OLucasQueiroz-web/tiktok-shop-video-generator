"""
Animação "respiração" do produto -- zoom que aumenta e diminui, com a
imagem sempre preenchendo o quadro inteiro (modo "cover" -- igual ao
CapCut quando você estica um clipe pra cobrir o quadro todo: o excedente é
cortado nas bordas, nunca sobra fundo preto).

BUG DO FFMPEG (build usada neste projeto) E A SOLUÇÃO
=======================================================
Testado exaustivamente (dezenas de renders isolados, medição de pixel a
pixel): a variável `on` (contador de frame) do zoompan -- e qualquer
expressão que dependa dela pra calcular o tempo -- NÃO atualiza direito
nesse build: o resultado fica CONGELADO. A auto-referência de uma variável
a ela mesma dentro da própria expressão (`zoom` referenciando `zoom` em
`z=`) funciona -- mas só CRESCENDO (`min(teto, zoom+delta)`); a versão
decrescente (`max(piso, zoom-delta)`) também trava.

Além disso, encadear `reverse` DIRETO depois de um `zoompan` alimentado por
uma imagem estática em loop infinito (`-loop 1`) trava o FFmpeg (o filtro
`reverse` nunca recebe o EOF que precisa pra saber quantos quadros
bufferizar) -- confirmado tentando isso isoladamente.

Solução final (só usa mecanismos comprovadamente confiáveis):
  1. Renderiza um clipe curto e FINITO em disco só com o crescimento
     (`zoompan` auto-referenciado crescendo, `-frames:v` finito -- sem
     `reverse` nessa etapa).
  2. Aplica `reverse` nesse ARQUIVO já pronto (uso padrão do filtro, com
     EOF de verdade -- funciona normalmente, diferente do caso acima).
  3. Concatena crescimento + encolhimento (arquivo reverso) num único
     "ciclo de respiração" (mp4 pequeno).
  4. No vídeo final, esse ciclo entra como mais um input, repetido via
     `-stream_loop -1` (a mesma técnica já usada pro GIF do cadeado em
     price_badge.py) -- sem nenhum zoompan "ao vivo" no comando principal.

GEOMETRIA
==========
O conteúdo é redimensionado no modo "cover" (`_cover_dimensions`) pra ser a
MENOR imagem que ainda cobre o painel inteiro -- ou seja, o canvas (=
conteúdo, `fw x fh`) é sempre >= painel nas duas dimensões. O zoompan então
recorta uma janela centralizada desse canvas, sempre menor ou igual ao
painel, e escala essa janela pro tamanho do painel:

  z=1.0    -> janela = painel inteiro -> cover padrão, imagem preenche a
              tela de ponta a ponta sem cortar demais (estado "recuado")
  z=teto   -> janela encolhe (painel/teto) -> mostra uma região menor do
              conteúdo, ampliada -- efeito de zoom "aproximando" (estado
              "perto")

Como o canvas nunca é menor que a janela em nenhum estado do zoom, nunca
aparece fundo preto -- só a imagem do produto, sempre preenchendo o quadro.
"""

import logging
import random
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from .config_loader import AppConfig
from .ffmpeg_utils import run_ffmpeg

logger = logging.getLogger(__name__)

# Encoding "lossless" pros passos intermediários (crescimento/reverso/
# concat) -- evita acumular artefato de compressão antes do encode final
# de verdade (feito por video_processor.py com config.codec/crf/preset).
_LOSSLESS_ARGS = ["-c:v", "libx264", "-preset", "veryfast", "-qp", "0", "-pix_fmt", "yuv420p", "-an"]


@dataclass
class KenBurnsParams:
    """Parâmetros prontos para o filter_complex/inputs do FFmpeg."""
    effect_type: str
    filters: List[str]          # filtros a inserir no filter_complex (modo estático)
    output_label: str           # ex.: "[bg_panel]"
    clip_path: Optional[str]    # ciclo de respiração pré-renderizado (modo animado)
    temp_dir: Optional[str]     # pasta temporária do clip_path -- limpar após o render final
    fw: int                     # dimensões do conteúdo "cover" (preenche o painel, corta o excedente)
    fh: int
    big_w: int                  # canvas (== fw/fh -- conteúdo cobre o painel inteiro, sem sobra)
    big_h: int
    pad_x: int                  # posição do conteúdo dentro do canvas (sempre 0 no modo cover)
    pad_y: int
    # Estado do zoompan em t=0 (todo ciclo começa em zoom=1.0) -- usado por
    # price_badge.py pra projetar a caixa do preço.
    x0: float = 0.0
    y0: float = 0.0
    z0: float = 1.0
    # Teto de zoom do ciclo de respiração (1.0 no modo estático -- sem
    # variação nenhuma). price_badge.py usa isso pra saber toda a faixa de
    # zoom [1.0, zoom_ceiling] que o badge fixo precisa continuar cobrindo.
    zoom_ceiling: float = 1.0


def _cover_dimensions(orig_w: int, orig_h: int, box_w: int, box_h: int) -> "tuple[int, int]":
    """Menor tamanho que ainda cobre box_w x box_h inteiramente (modo
    "cover" -- o excedente é cortado depois), preservando a proporção
    original."""
    fit_scale = max(box_w / orig_w, box_h / orig_h)
    fw = max(2, round(orig_w * fit_scale))
    fh = max(2, round(orig_h * fit_scale))
    fw -= fw % 2
    fh -= fh % 2
    return fw, fh


def build_ken_burns(
    config: AppConfig,
    duration: float,
    orig_w: int,
    orig_h: int,
    panel_w: int,
    panel_h: int,
    background_path: str,
    ffmpeg_path: str,
) -> KenBurnsParams:
    """
    `orig_w`/`orig_h`: dimensões reais da imagem do produto. `background_path`
    é necessário aqui (e não só em video_processor.py) porque, no modo
    animado, a respiração é pré-renderizada num arquivo à parte (ver
    docstring do módulo) -- precisa rodar FFmpeg diretamente.
    """
    fps  = config.fps
    anim = config.animation

    min_zoom = max(0.3, min(1.0, anim.min_zoom))
    ceiling  = 1.0 / min_zoom  # ex.: min_zoom=0.80 -> teto=1.25

    # -- Desabilitado ou teto~1.0: estático, cobrindo o quadro inteiro, sem zoom --
    if not anim.enabled or ceiling <= 1.001:
        fw, fh = _cover_dimensions(orig_w, orig_h, panel_w, panel_h)
        big_w, big_h = fw, fh  # canvas == conteúdo (cover, sem sobra) -- ver docstring
        pad_x = pad_y = 0
        static_filter = (
            f"[1:v]scale={fw}:{fh},"
            f"crop={panel_w}:{panel_h},"
            f"setpts=PTS-STARTPTS[bg_panel]"
        )
        logger.info("[ANIMATION] disabled -- imagem cobrindo o quadro, estática, sem zoom.")
        return KenBurnsParams(
            effect_type="static",
            filters=[static_filter], output_label="[bg_panel]",
            clip_path=None, temp_dir=None,
            fw=fw, fh=fh, big_w=big_w, big_h=big_h, pad_x=pad_x, pad_y=pad_y,
            x0=(big_w - panel_w) / 2.0, y0=(big_h - panel_h) / 2.0, z0=1.0,
        )

    # Conteúdo é a MENOR imagem que ainda cobre o painel inteiro (cover) --
    # assim, mesmo na janela mais larga do zoompan (z=1.0, painel inteiro),
    # a imagem já preenche o quadro de ponta a ponta, sem nunca sobrar preto.
    fw, fh = _cover_dimensions(orig_w, orig_h, panel_w, panel_h)
    big_w, big_h = fw, fh  # canvas == conteúdo (cover, sem sobra) -- ver docstring
    pad_x = pad_y = 0

    half_seconds = round(random.uniform(1.1, 1.5), 3)
    frames_per_half = max(2, round(half_seconds * fps))

    amplitude = ceiling - 1.0
    delta = round((amplitude / frames_per_half) * 1.05, 6)  # leve folga p/ garantir que bate no teto

    z_expr = f"min({ceiling:.4f},zoom+{delta:.6f})"
    x_expr = f"({big_w}-{panel_w}/zoom)/2"
    y_expr = f"({big_h}-{panel_h}/zoom)/2"

    tmp_dir = Path(tempfile.mkdtemp(prefix="kb_breathe_"))
    grow_path = str(tmp_dir / "grow.mp4")
    shrink_path = str(tmp_dir / "shrink.mp4")
    bounce_path = str(tmp_dir / "bounce.mp4")

    # 1) Crescimento (zoompan auto-referenciado, único mecanismo confiável
    #    -- ver docstring do módulo) -- clipe FINITO em disco.
    # `-frames:v` é o que realmente encerra o comando -- o `d=` do zoompan
    # sozinho NÃO interrompe o stream (a entrada `-loop 1` é infinita e o
    # zoompan segue repassando quadros pra sempre se nada mais cortar --
    # confirmado: sem esse limite explícito, o render nunca termina).
    run_ffmpeg(
        [
            ffmpeg_path, "-y",
            "-loop", "1", "-framerate", str(fps), "-i", background_path,
            "-vf",
            f"scale={fw}:{fh},"
            f"zoompan=z='{z_expr}':x='{x_expr}':y='{y_expr}'"
            f":d={frames_per_half}:s={panel_w}x{panel_h}:fps={fps}",
            "-frames:v", str(frames_per_half),
            *_LOSSLESS_ARGS,
            grow_path,
        ],
        description="respiração do produto -- pré-render (crescimento)",
    )

    # 2) Encolhimento = o MESMO clipe, com `reverse` aplicado a um arquivo
    #    já pronto (EOF de verdade -- uso padrão do filtro, confiável).
    run_ffmpeg(
        [ffmpeg_path, "-y", "-i", grow_path, "-vf", "reverse", *_LOSSLESS_ARGS, shrink_path],
        description="respiração do produto -- pré-render (encolhimento)",
    )

    # 3) Um ciclo completo (cresce + encolhe) -- repetido via -stream_loop -1
    #    no comando principal (ver video_processor.py).
    run_ffmpeg(
        [
            ffmpeg_path, "-y",
            "-i", grow_path, "-i", shrink_path,
            "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0[out]",
            "-map", "[out]", *_LOSSLESS_ARGS,
            bounce_path,
        ],
        description="respiração do produto -- pré-render (ciclo completo)",
    )

    logger.info(
        "[ANIMATION] style=breathing | zoom 1.000-%.3f | half=%.2fs (%d frames/metade) | "
        "fw=%d fh=%d panel=%dx%d | clip=%s",
        ceiling, half_seconds, frames_per_half, fw, fh, panel_w, panel_h, bounce_path,
    )

    return KenBurnsParams(
        effect_type="breathing",
        filters=[], output_label="",
        clip_path=bounce_path, temp_dir=str(tmp_dir),
        fw=fw, fh=fh, big_w=big_w, big_h=big_h, pad_x=pad_x, pad_y=pad_y,
        # Todo ciclo começa em zoom=1.0 -- t=0 do vídeo é sempre esse estado.
        x0=(big_w - panel_w) / 2.0, y0=(big_h - panel_h) / 2.0, z0=1.0,
        zoom_ceiling=ceiling,
    )
