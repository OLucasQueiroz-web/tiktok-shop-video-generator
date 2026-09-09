"""
Fábrica compartilhada do reader EasyOCR.

O modelo é pesado para carregar (alguns segundos + centenas de MB de RAM) --
carregar duas instâncias separadas (uma para o bloqueio de preço, outra para
a identificação de título) dobraria esse custo à toa. Quem precisa de OCR
(PriceBlocker, ProductTitleOCR) recebe um reader já pronto em vez de criar o
seu próprio.
"""

import logging
from typing import List, Optional

log = logging.getLogger("ocr-reader")


def create_reader(languages: Optional[List[str]] = None):
    """
    Cria e retorna uma instância de easyocr.Reader, ou None se a dependência
    não estiver instalada ou o carregamento falhar -- nesses casos, quem
    chama deve desabilitar graciosamente as funcionalidades que dependem de
    OCR (mesmo padrão de PriceBlocker.available / ProductIdentifier.available).
    """
    try:
        import easyocr  # import tardio -- dependência pesada (torch)
    except ImportError:
        log.warning(
            "easyocr não instalado -- funcionalidades de OCR desabilitadas. "
            "Rode: pip install easyocr"
        )
        return None

    try:
        log.info("Carregando modelo OCR (EasyOCR)... isso pode levar alguns segundos.")
        reader = easyocr.Reader(languages or ["pt"], gpu=False, verbose=False)
        log.info("Modelo OCR carregado.")
        return reader
    except Exception as exc:
        log.warning("Falha ao carregar EasyOCR: %s", exc)
        return None
