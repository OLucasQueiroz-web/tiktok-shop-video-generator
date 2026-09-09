"""
Product Title OCR -- TikTok Shop
=============================================
Identifica o nome do produto a partir da imagem via OCR (EasyOCR), sem IA
generativa: o título é texto real renderizado no print do anúncio, então dá
pra extraí-lo diretamente em vez de descrever a imagem para um modelo
multimodal (ProductIdentifier, ver product_identifier.py).

Como localizar o título sem entender a imagem
------------------------------------------------
O layout dos prints de produto (TikTok Shop) é consistente: o título fica
sempre entre o bloco de preço/parcelamento/cupom e a linha de avaliação
("4.8 (1,9 mil)" / "9700 vendidos"). Em vez de tentar classificar cada
trecho como "é o título", localizamos as DUAS âncoras (fim do bloco de
preço, início da linha de avaliação) e pegamos tudo que sobra no meio.

Duas passagens de OCR (normal + cores invertidas)
-----------------------------------------------------
Alguns cards de oferta renderizam o título em texto claro sobre fundo
escuro -- e o recognizer do EasyOCR erra sistematicamente esse caso
(confirmado em teste: 0 detecções na região, mesmo com upscaling). Rodar o
OCR de novo numa cópia com as cores invertidas resolve, então sempre
rodamos as duas passagens e combinamos os resultados.

Disponibilidade
----------------
Mesmo padrão de PriceBlocker: se não houver reader OCR (easyocr
indisponível), self.available = False e identify() sempre retorna None --
quem chama (ProductNamer) cai de volta para o Groq ou para o nome padrão
P{index}.
"""

import logging
import re
from pathlib import Path
from typing import List, Optional, Tuple

log = logging.getLogger("product-title-ocr")

_PRICE_RE = re.compile(r"(r\$|rs\b)?\s*\d{1,3}(?:[.,]{1,2}\d{1,3})+", re.IGNORECASE)
_PERCENT_RE = re.compile(r"^-?\s*\d{1,3}\s*%$")
_COUNTDOWN_RE = re.compile(r"\d{1,2}:\d{2}:\d{2}")
_OFERTA_RE = re.compile(r"\boferta\b", re.IGNORECASE)
_DIGIT_O_TOKEN_RE = re.compile(r"^(\d[\d.,oO]*)([a-zA-Z]{0,6})$")

# Termos com significado inequívoco de UI de preço/cupom/parcelamento --
# usados para achar o limite SUPERIOR da zona do título (onde o "ruído"
# antes do título termina). Fica de fora de tudo que não tem posição
# confiável (badges de loja, fragmentos curtos) para esses não empurrarem a
# fronteira pra dentro do próprio título por engano.
# Palavras/frases soltas com \b (fronteira de palavra) -- SEM isso, "compre"
# bate como substring de "Compressor" e engole o título inteiro (bug real,
# confirmado em produção: "Compressor de ar portátil..." virava ruído).
_BOUNDARY_NOISE_TERMS = [
    "desconto", "juros", "frete", "cupom", "compre", "ganhe",
    "vendidos", "avalia\\w*", "parcela\\w*", "receba", "grátis",
    "gratis", "economize", "termina em", "estoque limitado",
    "relâmpago", "relampago", "máximo de", "taxa de envio",
]
_BOUNDARY_NOISE_RE = re.compile(
    r"\b(" + "|".join(_BOUNDARY_NOISE_TERMS) + r")\b", re.IGNORECASE
)

# Ruído extra (badges de loja) que não deve aparecer no título final, mas
# que -- por não ter posição confiável -- nunca entra no cálculo da zona.
_EXTRA_NOISE_SUBSTRINGS = ["loja estrela", "loja oficial", "ofertas de marca"]

BBox = Tuple[float, float, float, float]


def _is_boundary_noise(text: str) -> bool:
    t = text.strip().lower()
    if not t:
        return True
    price_match = _PRICE_RE.search(t)
    if price_match and (price_match.end() - price_match.start()) / len(t) > 0.55:
        return True
    if _PERCENT_RE.match(t):
        return True
    if _COUNTDOWN_RE.search(t):
        return True
    if _OFERTA_RE.search(t):
        return True
    if _BOUNDARY_NOISE_RE.search(t):
        return True
    if re.match(r"^\d{1,2}x$", t):
        return True
    if re.match(r"^[a-z]\d$", t):  # ex.: 'e3'
        return True
    return False


def _is_noise(text: str) -> bool:
    """Filtro por caixa (mais amplo que _is_boundary_noise): decide se uma
    caixa específica entra no texto final do título. NUNCA usar para
    calcular a zona do título (só _is_boundary_noise faz isso)."""
    t = text.strip().lower()
    if not t:
        return True
    if len(t) <= 2:
        return True
    if _is_boundary_noise(t):
        return True
    for sub in _EXTRA_NOISE_SUBSTRINGS:
        if sub in t:
            return True
    return False


def _is_rating_anchor(text: str) -> bool:
    """Linha de avaliação/vendas -- limite INFERIOR da zona do título.
    'vendidos' é o sinal mais confiável (o formato da nota numérica varia
    muito e o OCR erra o separador decimal com frequência)."""
    t = text.strip().lower()
    if "vendidos" in t:
        return True
    if re.match(r"^\d[.,]\.?\d\s*\(.+\)$", t):
        return True
    return False


def _fix_digit_o_confusion(token: str) -> str:
    """EasyOCR troca '0' por 'O'/'o' com frequência em tokens numéricos
    grudados numa unidade (ex.: '300ml' -> '3OOml'). Só mexe em tokens que
    começam com dígito -- marcas/palavras normais nunca entram aqui."""
    m = _DIGIT_O_TOKEN_RE.match(token)
    if not m:
        return token
    num_part, suffix = m.groups()
    return re.sub(r"[oO]", "0", num_part) + suffix


class ProductTitleOCR:
    """
    Extrai o nome do produto de uma imagem via OCR, localizando a zona de
    texto entre o bloco de preço e a linha de avaliação. Reutiliza uma
    instância de reader OCR compartilhada (ver src/ocr_reader.py).
    """

    def __init__(self, reader=None) -> None:
        self.available: bool = reader is not None
        self._reader = reader
        if self.available:
            # Não afirma que a NOMEAÇÃO via OCR está ativa aqui -- quem
            # decide isso é ProductNamer (config.product_identification.method).
            # Esta classe também é usada só pra localizar a faixa do título
            # (identify_with_band()) quando o método de nomeação é "groq".
            log.info("OCR de título disponível (nomeação e/ou localização da faixa do título).")
        else:
            log.warning("Nenhum reader OCR disponível -- OCR de título (nomeação e localização) desabilitado.")

    def _detect(self, img) -> List[dict]:
        out = []
        for bbox, text, conf in self._reader.readtext(img, width_ths=0.3):
            xs = [p[0] for p in bbox]
            ys = [p[1] for p in bbox]
            out.append({
                "x1": min(xs), "x2": max(xs), "y1": min(ys), "y2": max(ys),
                "text": text, "conf": conf,
            })
        return out

    def identify(self, image_path: Path) -> Optional[str]:
        """
        Retorna o título do produto extraído da imagem, ou None se a zona
        do título não puder ser localizada com confiança (crop cortado
        antes do título, título ausente, etc.) -- quem chama deve cair de
        volta para outro método de identificação (Groq) ou o nome padrão.
        """
        result = self._analyze(image_path)
        return result["title"] if result else None

    def identify_with_band(self, image_path: Path) -> Tuple[Optional[str], Optional[BBox]]:
        """
        Como identify(), mas também retorna a caixa (x1,y1,x2,y2, em pixels
        da imagem ORIGINAL) da zona onde o título foi localizado -- usada
        para manter o avatar de cobrir essa área no vídeo (ver
        src/avatar_pose.py + project_box_to_screen() em src/price_badge.py,
        que já faz a mesma projeção pro badge do cadeado). Retorna
        (None, None) se a zona não puder ser localizada.
        """
        result = self._analyze(image_path)
        if result is None:
            return None, None
        return result["title"], result["box"]

    def _analyze(self, image_path: Path) -> Optional[dict]:
        """
        Roda o OCR uma única vez e retorna {"title": str, "box": BBox}, ou
        None se a zona do título não puder ser localizada -- compartilhado
        por identify() e identify_with_band() pra não rodar OCR em dobro.
        """
        if not self.available:
            return None

        from PIL import Image, ImageOps
        import numpy as np

        try:
            im = Image.open(image_path).convert("RGB")
            dets = self._detect(np.array(im)) + self._detect(np.array(ImageOps.invert(im)))
        except Exception as exc:
            log.warning("  [title-ocr] Falha ao rodar OCR em '%s': %s", Path(image_path).name, exc)
            return None

        # dedup: as duas passagens às vezes acertam a mesma região -- fica
        # com a de maior confiança.
        dets.sort(key=lambda d: -d["conf"])
        kept: List[dict] = []
        for d in dets:
            if any(abs(d["y1"] - k["y1"]) < 6 and abs(d["x1"] - k["x1"]) < 20 for k in kept):
                continue
            kept.append(d)
        dets = kept

        rating_candidates = [d for d in dets if _is_rating_anchor(d["text"])]
        y_rating_top = min((d["y1"] for d in rating_candidates), default=None)

        # Sem âncora de rating (crop cortado antes da linha de vendas): usa
        # um teto de segurança acima do último ruído de preço/cupom em vez
        # de "sem limite" -- título raramente passa de ~140px (2-3 linhas),
        # isso evita que ruído bem mais abaixo contamine o cálculo.
        MAX_TITLE_BAND_PX = 140
        y_max = y_rating_top if y_rating_top is not None else max((d["y2"] for d in dets), default=0) + 1

        noise_above = [d for d in dets if _is_boundary_noise(d["text"]) and d["y2"] < y_max]
        y_min = max((d["y2"] for d in noise_above), default=0)

        if y_rating_top is None:
            y_max = min(y_max, y_min + MAX_TITLE_BAND_PX)

        title_boxes = [
            d for d in dets
            if not _is_noise(d["text"]) and d["y1"] >= y_min - 3 and d["y2"] <= y_max + 3
        ]
        if not title_boxes:
            log.info("  [title-ocr] Zona do título não encontrada em '%s'.", Path(image_path).name)
            return None

        title_boxes.sort(key=lambda d: (round(d["y1"] / 8), d["x1"]))
        title = " ".join(d["text"].strip() for d in title_boxes)
        title = re.sub(r"\s+", " ", title).strip().rstrip(".").strip()
        title = " ".join(_fix_digit_o_confusion(tok) for tok in title.split(" "))

        if len(title) < 4:
            log.info("  [title-ocr] Título extraído curto demais em '%s': %r", Path(image_path).name, title)
            return None

        log.info("  [title-ocr] '%s' -> %r", Path(image_path).name, title)
        box: BBox = (
            min(d["x1"] for d in title_boxes),
            min(d["y1"] for d in title_boxes),
            max(d["x2"] for d in title_boxes),
            max(d["y2"] for d in title_boxes),
        )
        return {"title": title, "box": box}
