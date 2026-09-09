"""
Price Blocker -- TikTok Shop
=============================================
Localiza o preço na imagem do produto via OCR (EasyOCR) e "assa" uma barra
pixelada/escurecida por cima da região detectada. O ícone animado (cadeado
pulsando em GIF) que fica visualmente por cima dessa barra é responsabilidade
de price_badge.py -- ver o motivo da divisão logo abaixo.

Motivação
---------
  1. Evita problemas de "preço enganoso" no TikTok: o vídeo pode ficar no ar
     depois que o preço do anúncio já mudou -- se o preço não aparece
     legível no vídeo, isso deixa de ser um risco.
  2. Gera curiosidade: um preço "trancado" convida o espectador a continuar
     assistindo / clicar no link para descobrir o valor.

Por que a pixelização continua sendo "assada" no PNG (e o cadeado virou GIF)
------------------------------------------------------------------------------
  O fundo já passa por uma animação contínua de zoom/pan (Ken Burns, ver
  ken_burns.py) via zoompan. A pixelização precisa cobrir os dígitos reais do
  preço -- que são únicos por imagem -- então ela só pode ser gerada por
  produto, e por isso continua sendo assada direto nos pixels da imagem de
  fundo ANTES dela entrar no FFmpeg: o zoompan anima a imagem inteira, então
  a barra "herda" o mesmo zoom/pan/tremor de graça.
  Já o cadeado (ícone genérico, igual em todo vídeo) *pode* ser um GIF
  animado real, desde que sua posição na tela seja calculada -- não
  sincronizada quadro a quadro. `price_badge.py` projeta a caixa do preço
  (calculada aqui, em coordenadas da imagem original) para a posição de tela
  já esperada em t=0 do zoompan e sobrepõe o GIF ali via overlay do FFmpeg,
  evitando duplicar as expressões z/x/y do zoompan.

Disponibilidade
----------------
  Assim como ProductIdentifier, se as dependências (easyocr/PIL/numpy) não
  estiverem instaladas, self.available = False e apply_lock() sempre
  retorna None -- o chamador cai de volta para a imagem original, sem
  quebrar o restante do pipeline.
"""

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

log = logging.getLogger("price-blocker")

# Número no formato de preço: dígitos + separador decimal com 1-3 casas.
# Tolera 1-2 caracteres de separador em sequência ([.,]{1,2}) porque o OCR
# eventualmente confunde um ícone/símbolo colado ao número com uma vírgula
# extra (ex.: "21,90" virar "21,,0", ou pior, com o "R$" grudado errado).
# NÃO exige "R$"/"Rs" aqui -- essa checagem é feita à parte em
# _has_currency_context(), porque o OCR às vezes separa o símbolo dos
# dígitos em duas caixas de texto diferentes (ex.: "Rs" numa caixa e
# "444,,9" noutra, lado a lado) -- exigir o prefixo no mesmo texto faria
# esse preço passar batido.
_NUMBER_RE = re.compile(r"\d{1,3}(?:[.,]{1,2}\d{1,3})+")

# Fallback para quando o OCR "come" o separador decimal (comum em preço
# riscado/baixa resolução -- ex.: "149,90" virar "14990", sem vírgula
# nenhuma). Um run de 3+ dígitos só conta como preço quando o símbolo de
# moeda está no MESMO texto (has_marker_in_text) -- senão contagens
# genéricas coladas a outro texto (ex.: "4219 vendidos") seriam blocladas
# por engano.
_BARE_DIGITS_RE = re.compile(r"\d{3,}")

# Fragmento "órfão" de centavos: caixa de texto que COMEÇA com separador
# decimal + 1-3 dígitos (ex.: ",99", ".90"), sem a parte inteira na mesma
# caixa. Acontece quando o OCR separa os dígitos inteiros dos centavos em
# caixas diferentes (fontes de tamanho diferente), OU quando o próprio app
# já esconde a parte inteira atrás de reticências (ex.: "A partir de R$
# ... ,99" -- truncamento nativo do TikTok Shop, não do OCR). Em ambos os
# casos os centavos sozinhos ainda são preço legível e precisam ser
# bloqueados -- ver o segundo passo em _find_price_boxes().
_DECIMAL_TAIL_RE = re.compile(r"^[.,]\d{1,3}\b")

# Último fallback: separador decimal + 1-3 dígitos EM QUALQUER PONTO do
# texto (não precisa ser o texto inteiro, ver _DECIMAL_TAIL_RE acima, nem
# 3+ dígitos seguidos, ver _BARE_DIGITS_RE acima). Cobre o caso do OCR
# "engolir" os dígitos inteiros do preço e sobrar só um fragmento curto
# colado ao resto da frase (ex.: "partir de R$ .48 8" -- os dígitos
# inteiros nunca foram lidos, só sobrou ".48"). Só usado quando o símbolo
# de moeda está no MESMO texto (has_marker_in_text) -- mesma trava de
# segurança dos outros fallbacks, pra não bloquear um decimal qualquer sem
# relação com preço.
_DECIMAL_FRAGMENT_ANYWHERE_RE = re.compile(r"[.,]\d{1,3}")

# Caixa de texto que TERMINA em "R$"/"Rs" (sozinho, ex.: "Rs", ou grudado a
# outro texto, ex.: "A partir de R$") -- usada para achar um marcador de
# moeda que ficou numa caixa diferente da dos dígitos, mas logo à esquerda
# deles (o OCR corta a caixa exatamente onde o layout visual quebra).
_CURRENCY_SUFFIX_RE = re.compile(r"(?:r\$|rs)\W*$", re.IGNORECASE)

# Símbolo de moeda em qualquer lugar do texto (mesmo colado a outra coisa).
# NÃO usa \b depois de "rs" -- dígito também conta como caractere de
# palavra pra regex, então "Rs4999" (OCR grudando o "Rs" nos dígitos sem
# espaço nem vírgula, caso comum em baixa confiança) nunca bateria com
# "rs\b" e a caixa inteira seria ignorada, vazando o preço inteiro. Em vez
# disso, exige só que NENHUMA LETRA venha imediatamente antes/depois de
# "rs" (dígito, espaço, pontuação ou início/fim de string são permitidos)
# -- ainda evita falso positivo em palavras como "diversos".
_LETTER = r"[A-Za-zÀ-ÖØ-öø-ÿ]"
_CURRENCY_ANYWHERE_RE = re.compile(
    r"r\$|(?<!" + _LETTER + r")rs(?!" + _LETTER + r")", re.IGNORECASE
)

BBox = Tuple[float, float, float, float]  # (x1, y1, x2, y2)


def _hex_to_rgba(hex_color: str, alpha: int = 255) -> Tuple[int, int, int, int]:
    h = hex_color.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return r, g, b, alpha


@dataclass
class PriceLockResult:
    """
    Resultado de apply_lock(): caminho da imagem com a barra pixelizada
    assada + a caixa (com padding, mesmas coordenadas usadas para desenhar a
    barra) na imagem ORIGINAL (antes de qualquer resize), para que
    price_badge.py possa projetar essa região para a tela de saída e
    posicionar o GIF do cadeado por cima.
    """
    image_path: str
    box: BBox
    orig_size: Tuple[int, int]  # (width, height) da imagem original


class PriceBlocker:
    """
    Detecta e bloqueia visualmente o preço em destaque de uma imagem de
    produto. Reutiliza uma única instância do reader OCR (custosa de
    carregar) para todas as imagens processadas na execução.
    """

    def __init__(self, enabled: bool = True, reader=None) -> None:
        """
        `reader`: instância de easyocr.Reader já carregada (ver
        src/ocr_reader.py) -- compartilhada com ProductTitleOCR quando ambos
        estão ativos, para não carregar o modelo (pesado) duas vezes.
        """
        self.available: bool = False
        self._reader = reader

        if not enabled:
            log.info("Bloqueio de preço desabilitado via config (price_lock.enabled=false).")
            return

        if reader is None:
            log.warning("Nenhum reader OCR disponível -- bloqueio de preço desabilitado.")
            return

        self.available = True
        log.info("Bloqueio de preço ativo (OCR compartilhado).")

    # -- Detecção ------------------------------------------------------------

    @staticmethod
    def _find_adjacent_currency_marker(
        box: BBox, currency_boxes: List[BBox],
    ) -> Optional[BBox]:
        """
        Procura uma caixa que TERMINA em "R$"/"Rs" (currency_boxes --
        isolada, ex.: "Rs", ou grudada a outro texto, ex.: "A partir de R$")
        imediatamente à ESQUERDA de `box`, com sobreposição vertical
        significativa -- cobre o caso do OCR separar o símbolo de moeda dos
        dígitos em duas caixas de texto lado a lado (ex.: "Rs" + "444,,9",
        ou "A partir de R$" + "32,33").
        """
        x1, y1, x2, y2 = box
        h = y2 - y1
        if h <= 0:
            return None
        for mx1, my1, mx2, my2 in currency_boxes:
            overlap = min(y2, my2) - max(y1, my1)
            if overlap < h * 0.4:
                continue
            gap = x1 - mx2  # distância do fim do marcador até o início do número
            # Tolera sobreposição horizontal leve (gap negativo) entre as
            # duas caixas -- o OCR não corta exatamente no limite visual do
            # texto, então "R$" e o número podem se sobrepor por alguns
            # pixels mesmo estando em caixas diferentes.
            if -h * 0.3 <= gap <= h * 1.5:
                return (mx1, my1, mx2, my2)
        return None

    def _find_price_boxes(self, image_path: Path, config) -> List[BBox]:
        import numpy as np
        from PIL import Image

        img = np.array(Image.open(image_path).convert("RGB"))
        # width_ths baixo evita que o OCR agrupe o badge de desconto (“-41%”)
        # junto com o preço na mesma caixa de texto -- só que isso também faz
        # o OCR separar o "R$" dos dígitos às vezes (ver currency_marker_boxes).
        results = self._reader.readtext(img, width_ths=config.ocr_width_ths)

        text_boxes: List[Tuple[str, BBox]] = []
        currency_marker_boxes: List[BBox] = []
        decimal_tail_boxes: List[BBox] = []
        for bbox, text, conf in results:
            if conf < config.ocr_min_confidence:
                continue
            xs = [p[0] for p in bbox]
            ys = [p[1] for p in bbox]
            box = (float(min(xs)), float(min(ys)), float(max(xs)), float(max(ys)))
            text_boxes.append((text, box))
            if _CURRENCY_SUFFIX_RE.search(text.strip()):
                currency_marker_boxes.append(box)
            if _DECIMAL_TAIL_RE.match(text.strip()):
                decimal_tail_boxes.append(box)

        candidates: List[Tuple[float, BBox]] = []
        for text, (x1, y1, x2, y2) in text_boxes:
            # Pega TODOS os trechos que parecem número de preço no texto:
            # quando o OCR gruda "-41% R$ 65,74" (ou pior, "Rs 28,30 R$
            # 28,88" com o preço riscado colado no mesmo texto) numa caixa
            # só, cada preço vira um candidato separado -- não só o primeiro.
            matches = list(_NUMBER_RE.finditer(text))

            has_marker_in_text = bool(_CURRENCY_ANYWHERE_RE.search(text))

            if not matches and has_marker_in_text:
                # O símbolo de moeda está no texto, mas nenhum separador
                # decimal foi lido -- o OCR provavelmente "comeu" a vírgula
                # (comum em preço riscado/baixa resolução, ex.: "RS 14990"
                # em vez de "RS 149,90"). Um run de 3+ dígitos ainda conta
                # como preço aqui porque está preso ao marcador de moeda.
                matches = list(_BARE_DIGITS_RE.finditer(text))

            if not matches and has_marker_in_text:
                # Nem número completo, nem 3+ dígitos seguidos -- o OCR
                # comeu os dígitos inteiros e só sobrou um fragmento curto
                # de centavos colado ao resto da frase (ex.: "partir de R$
                # .48 8"). Último fallback: separador + 1-3 dígitos em
                # qualquer ponto do texto, ainda preso ao marcador de moeda.
                matches = list(_DECIMAL_FRAGMENT_ANYWHERE_RE.finditer(text))

            if not matches:
                continue

            width = x2 - x1
            text_len = len(text)

            for match in matches:
                mx1, mx2 = x1, x2
                # Se o número não ocupa o texto inteiro (ex.: "-41% Rs
                # 65,74", ou "Rs 28,30 R$ 28,88" com dois preços), estreita a
                # caixa horizontalmente para a fração correspondente ao
                # trecho casado -- evita cobrir texto de outro preço/badge junto.
                if text_len > 0 and (match.start() > 0 or match.end() < text_len):
                    frac_start = match.start() / text_len
                    frac_end = match.end() / text_len

                    if has_marker_in_text:
                        # A fonte do preço costuma ser maior/mais grossa que
                        # o texto ao redor quando os dois vêm na MESMA caixa
                        # de OCR (ex.: "A partir de R$ 29,90" tudo junto) --
                        # a fração de caracteres do número assume largura
                        # uniforme e subestima onde os dígitos realmente
                        # começam, vazando o primeiro dígito sem bloqueio
                        # nenhum. Corta pelo INÍCIO do próprio símbolo de
                        # moeda em vez do início do número -- garante cobrir
                        # tudo a partir do "R$"/"Rs" (a pequena sobra sobre o
                        # símbolo em si é só cosmética).
                        marker_match = _CURRENCY_ANYWHERE_RE.search(text)
                        if marker_match is not None:
                            frac_start = min(frac_start, marker_match.start() / text_len)

                    new_x1 = x1 + width * frac_start
                    new_x2 = x1 + width * frac_end
                    if new_x2 - new_x1 >= 4:
                        mx1, mx2 = new_x1, new_x2

                if has_marker_in_text:
                    # "R$"/"Rs" está no próprio texto -- claramente um preço.
                    candidates.append((y2 - y1, (mx1, y1, mx2, y2)))
                    continue

                # Sem símbolo de moeda no próprio texto: só conta como preço
                # se houver uma caixa terminando em "R$"/"Rs" logo à esquerda
                # (ver _find_adjacent_currency_marker). Sem isso, é só um
                # número decimal qualquer (nota de avaliação "4.5", peso
                # "1,5kg" etc.) -- não bloqueia.
                marker = self._find_adjacent_currency_marker((mx1, y1, mx2, y2), currency_marker_boxes)
                if marker is None:
                    continue
                merged = (
                    min(mx1, marker[0]), min(y1, marker[1]),
                    max(mx2, marker[2]), max(y2, marker[3]),
                )
                candidates.append((merged[3] - merged[1], merged))

        # Segundo passo: fragmentos "órfãos" de centavos (ex.: ",99"), cuja
        # própria caixa de texto não tem símbolo de moeda nem dígitos
        # inteiros -- por isso nunca batem no loop acima. Só contam como
        # preço se estiverem na mesma linha (boa sobreposição vertical) de
        # algum marcador de moeda OU de um preço já confirmado -- evita
        # bloquear um decimal solto que não tem nada a ver com preço.
        row_reference_boxes = list(currency_marker_boxes) + [box for _, box in candidates]
        for dx1, dy1, dx2, dy2 in decimal_tail_boxes:
            dh = dy2 - dy1
            if dh <= 0:
                continue
            if any(
                min(dy2, ry2) - max(dy1, ry1) >= dh * 0.4
                for rx1, ry1, rx2, ry2 in row_reference_boxes
            ):
                candidates.append((dh, (dx1, dy1, dx2, dy2)))

        if not candidates:
            return []

        # Ordena por altura de caixa (fonte maior primeiro) -- o preço em
        # destaque ("hero", índice 0) segue sendo o de maior fonte, usado
        # por apply_lock() para posicionar o cadeado animado. Mas TODOS os
        # candidatos são retornados e bloqueados: preço riscado (De/Por) e
        # parcelamento (ex.: "12x R$ 5,99") não podem ficar legíveis.
        candidates.sort(key=lambda c: c[0], reverse=True)
        return [box for _, box in candidates]

    # -- Desenho do selo -------------------------------------------------------

    @staticmethod
    def _pixelate(region, block: int):
        from PIL import Image
        w, h = region.size
        small = region.resize((max(1, w // block), max(1, h // block)), Image.NEAREST)
        return small.resize((w, h), Image.NEAREST)

    def _bake_bar(self, image, box: BBox, config) -> Optional[BBox]:
        """
        Desenha a barra pixelizada/tintada (sem cadeado -- isso agora é o GIF
        animado de price_badge.py) direto no objeto `image` (RGBA, in-place).
        Retorna a caixa com padding realmente desenhada (coordenadas da
        própria `image`), para price_badge.py projetar o GIF por cima dela.
        """
        from PIL import Image, ImageDraw

        x1, y1, x2, y2 = box
        w, h = x2 - x1, y2 - y1
        pad_x, pad_y = w * config.padding_ratio, h * config.padding_ratio
        bx1 = max(0, x1 - pad_x)
        by1 = max(0, y1 - pad_y)
        bx2 = min(image.width, x2 + pad_x)
        by2 = min(image.height, y2 + pad_y)
        bw, bh = bx2 - bx1, by2 - by1
        if bw < 4 or bh < 4:
            return None

        region = image.crop((bx1, by1, bx2, by2)).convert("RGB")
        block = max(2, int(bh * config.pixel_block_ratio))
        mosaic = self._pixelate(region, block=block).convert("RGBA")

        bar_tint = _hex_to_rgba(config.bar_color, config.overlay_alpha)
        tint_layer = Image.new("RGBA", mosaic.size, bar_tint)
        bar = Image.alpha_composite(mosaic, tint_layer)

        mask = Image.new("L", bar.size, 0)
        ImageDraw.Draw(mask).rounded_rectangle(
            [0, 0, bar.size[0] - 1, bar.size[1] - 1],
            radius=min(bar.size) * 0.22, fill=255,
        )
        bar.putalpha(mask)

        image.alpha_composite(bar, (int(bx1), int(by1)))
        return (bx1, by1, bx2, by2)

    # -- API pública -------------------------------------------------------

    def apply_lock(self, image_path: str, config, cache_dir: Path) -> Optional[PriceLockResult]:
        """
        Detecta TODOS os preços em `image_path` -- preço em destaque, preço
        riscado (De/Por) e parcelamento (ex.: "12x R$ 5,99") -- e, se algum
        for encontrado, grava uma cópia com uma barra de bloqueio sobre cada
        um em `cache_dir`. Retorna o caminho da cópia + a caixa do preço em
        destaque (coordenadas da imagem original), usada por price_badge.py
        para posicionar o GIF do cadeado por cima dela -- as demais caixas
        (riscado/parcelado) recebem a mesma barra pixelizada/tintada, mas
        sem o cadeado, para não poluir a tela com vários ícones. Retorna
        None se nenhum preço for encontrado, o OCR estiver indisponível, ou
        algo falhar -- nesses casos o chamador deve usar a imagem original
        sem modificações e sem badge.
        """
        if not self.available:
            return None

        from PIL import Image

        path = Path(image_path)
        try:
            boxes = self._find_price_boxes(path, config)
            if not boxes:
                log.info("  [price-lock] Nenhum preço detectado em '%s' -- imagem original mantida.", path.name)
                return None

            img = Image.open(path).convert("RGBA")
            orig_size = img.size

            bar_boxes: List[Tuple[int, BBox]] = []
            for idx, box in enumerate(boxes):
                bar_box = self._bake_bar(img, box, config)
                if bar_box is not None:
                    bar_boxes.append((idx, bar_box))

            if not bar_boxes:
                log.info("  [price-lock] Caixa(s) de preço degenerada(s) em '%s' -- imagem original mantida.", path.name)
                return None

            # boxes[0] é o preço em destaque (maior fonte, ver _find_price_boxes) --
            # usa essa caixa pro cadeado se ela não tiver sido descartada por
            # degenerada; caso contrário, cai pra primeira caixa bloqueada com sucesso.
            hero_bar_box = next((bb for i, bb in bar_boxes if i == 0), bar_boxes[0][1])

            cache_dir.mkdir(parents=True, exist_ok=True)
            out_path = cache_dir / f"{path.stem}__locked{path.suffix}"
            img.save(out_path)
            log.info(
                "  [price-lock] %d preço(s) bloqueado(s) em '%s' -> %s",
                len(bar_boxes), path.name, out_path.name,
            )
            return PriceLockResult(image_path=str(out_path), box=hero_bar_box, orig_size=orig_size)

        except Exception as exc:
            log.warning("  [price-lock] Falha ao bloquear preço de '%s': %s", path.name, exc)
            return None
