"""
Product Namer -- escolhe como identificar o nome do produto e aplica o
fallback entre os métodos disponíveis.

Motivação: identificar o produto via OCR (ProductTitleOCR) não depende de
IA/chave de API/internet e, testado contra ~275 imagens já processadas
(nome real no arquivo), acerta o título com boa precisão -- mas OCR pode
falhar num crop pontual (título fora da imagem, fonte ilegível, etc.).
Nesses casos, caímos de volta para o Groq (ProductIdentifier) se
disponível, e para None (nome padrão P{index}, ver file_manager.py) se nem
esse estiver.

config.product_identification.method controla o método PRINCIPAL de NOMEAÇÃO:
  "ocr"   -- tenta OCR primeiro, cai para Groq se falhar (padrão)
  "groq"  -- usa só o Groq pra nomear (comportamento anterior)

Independente do método de nomeação, o OCR (quando há reader disponível --
ver src/ocr_reader.py, compartilhado com o bloqueio de preço) também é
usado SÓ pra localizar a faixa (bounding box) onde o título está impresso
na imagem, via identify_with_band() -- serve pra manter o avatar de tampar
o nome do produto no vídeo gerado (ver src/avatar_pose.py), mesmo quando
method="groq" (o Groq não localiza posição, só descreve o texto).
"""

import logging
from pathlib import Path
from typing import Optional, Tuple

from .product_identifier import ProductIdentifier
from .product_title_ocr import BBox, ProductTitleOCR

log = logging.getLogger("product-namer")


class ProductNamer:
    """
    Wrapper único usado pelo main.py no lugar de ProductIdentifier direto.
    `available` é True se PELO MENOS UM dos métodos (OCR ou Groq) puder ser
    usado -- mantém o mesmo contrato que o código já espera
    (`if namer.available: ...`).
    """

    def __init__(self, method: str, reader=None) -> None:
        self.method = method
        self._use_ocr_naming = method == "ocr"
        # Sempre construído quando há reader (independente do método de
        # NOMEAÇÃO) -- identify() só usa isso pra nomear quando method=="ocr"
        # (ver _use_ocr_naming acima), mas identify_with_band() sempre tenta
        # localizar a faixa do título com isso, mesmo em method="groq".
        self._ocr = ProductTitleOCR(reader)
        self._groq = ProductIdentifier()
        self.available = (self._use_ocr_naming and self._ocr.available) or self._groq.available

        if self._use_ocr_naming and not self._ocr.available:
            log.info("OCR indisponível -- identificação de produto usará só o Groq (se configurado).")

    def identify(self, image_path: Path) -> Optional[str]:
        """
        Retorna o nome do produto, ou None se nenhum método disponível
        conseguir identificá-lo -- o chamador já trata None como "usa o
        nome padrão P{index}" (mesmo comportamento de antes).
        """
        if self._use_ocr_naming and self._ocr.available:
            title = self._ocr.identify(image_path)
            if title:
                return title
            log.info(
                "  [product-namer] OCR não encontrou título em '%s' -- tentando Groq...",
                Path(image_path).name,
            )

        if self._groq.available:
            try:
                return self._groq.identify(image_path)
            except Exception as exc:
                log.warning("  [product-namer] Groq falhou: %s", exc)

        return None

    def identify_with_band(self, image_path: Path) -> Tuple[Optional[str], Optional[BBox]]:
        """
        Como identify(), mas também retorna a caixa (x1,y1,x2,y2, pixels da
        imagem ORIGINAL) da zona onde o título foi localizado -- usada por
        main.py/video_processor.py para manter o avatar de cobrir essa área
        no vídeo gerado (ver src/avatar_pose.py).

        A localização da faixa SEMPRE tenta usar o OCR quando há reader
        disponível, mesmo com method="groq" (o Groq nomeia mas não localiza
        posição) -- só a escolha de qual texto vira o NOME do produto segue
        method (igual identify()). Se o reader não estiver disponível, ou o
        OCR não achar a zona do título, a caixa volta None e o avatar usa a
        posição padrão configurada.
        """
        if self._use_ocr_naming and self._ocr.available:
            title, box = self._ocr.identify_with_band(image_path)
            if title:
                return title, box
            log.info(
                "  [product-namer] OCR não encontrou título em '%s' -- tentando Groq "
                "(ainda tenta localizar a faixa do título separadamente).",
                Path(image_path).name,
            )

        name = None
        if self._groq.available:
            try:
                name = self._groq.identify(image_path)
            except Exception as exc:
                log.warning("  [product-namer] Groq falhou: %s", exc)

        # Nomeação não veio do OCR (method="groq", ou OCR não achou título
        # acima) -- ainda assim tenta localizar a faixa do título separada
        # (só a caixa, sem reaproveitar o texto pra nomear) pra manter o
        # avatar de tampar o nome no vídeo, independente de quem nomeou.
        band = None
        if self._ocr.available:
            _, band = self._ocr.identify_with_band(image_path)

        return name, band
