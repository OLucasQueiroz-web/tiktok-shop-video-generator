"""
Product Identifier -- TikTok Shop
Identifica o nome do produto a partir da imagem usando a API multimodal do Groq.
"""

import base64
import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Optional

from groq import Groq

log = logging.getLogger("product-identifier")

MAX_RETRIES:           int   = 3
RETRY_BASE_DELAY:      float = 2.0
RATE_LIMIT_DELAY:      float = 60.0  # aguarda este tempo quando TODAS as chaves ativas
                                      # estão em rate limit por minuto no mesmo ciclo
MAX_RATE_LIMIT_CYCLES: int   = 10    # segurança contra loop infinito se o rate limit
                                      # por minuto persistir ciclo após ciclo

_KEY_VAR_RE = re.compile(r"^GROQ_API_KEY(\d*)$")

# Sinaliza qual JANELA de rate limit foi atingida -- Groq inclui isso na
# mensagem de erro (ex.: "on requests per day (RPD)" vs "on tokens per
# minute (TPM)"). "day" (RPD/TPD) é definitivo até o reset diário da conta
# -- a chave é descartada pelo resto da execução. "minute" (RPM/TPM) é
# transitório -- só pula para a próxima chave, podendo voltar depois.
_DAY_LIMIT_RE    = re.compile(r"per day|\brpd\b|\btpd\b", re.IGNORECASE)
_MINUTE_LIMIT_RE = re.compile(r"per minute|\brpm\b|\btpm\b", re.IGNORECASE)
# Fallback quando a mensagem não menciona a janela explicitamente: usa o
# tempo de espera sugerido ("please try again in 1h2m3.4s") -- acima de 10
# minutos tratamos como limite diário, abaixo como por minuto.
_RETRY_AFTER_RE = re.compile(
    r"try again in\s+(?:(\d+)h)?\s*(?:(\d+)m(?!s))?\s*([\d.]+)?s?", re.IGNORECASE
)
_DAY_LIMIT_THRESHOLD_SECONDS: float = 600.0


def _load_api_keys() -> list[str]:
    """
    Coleta todas as chaves GROQ_API_KEY, GROQ_API_KEY2, GROQ_API_KEY3, ... do
    ambiente (nessa ordem), permitindo alternar entre múltiplas contas/chaves
    quando uma delas atinge o limite de requisições (rate limit).
    """
    found: list[tuple[int, str]] = []
    for var_name, value in os.environ.items():
        if not value:
            continue
        match = _KEY_VAR_RE.match(var_name)
        if match:
            suffix = match.group(1)
            order = int(suffix) if suffix else 1
            found.append((order, value))

    found.sort(key=lambda item: item[0])
    return [value for _, value in found]

GROQ_MODEL: str = os.getenv("GROQ_MODEL", "qwen/qwen3.6-27b")

_MIME_MAP: dict[str, str] = {
    ".jpg":  "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png":  "image/png",
    ".webp": "image/webp",
}

_PROMPT = (
    "Esta imagem é um print de uma página/anúncio de produto de e-commerce "
    "(ex.: TikTok Shop). Ela contém o TÍTULO REAL do produto, exatamente como "
    "cadastrado na loja -- normalmente em texto preto/cinza escuro, perto do "
    "preço, às vezes em 2 linhas (ex.: 'Bettdow Smartwatch com GPS, 1.43\" "
    "Display Bluetooth Telefone Cham...').\n\n"
    "Transcreva ESSE título LITERALMENTE, palavra por palavra, exatamente "
    "como está escrito na imagem -- NÃO resuma, NÃO parafraseie, NÃO invente, "
    "NÃO traduza e NÃO use frases de banner/propaganda (chamadas promocionais "
    "grandes e coloridas, badges de desconto, textos de destaque no topo da "
    "imagem). Ignore qualquer texto que não seja o título de cadastro do "
    "produto.\n\n"
    "Se o título estiver cortado com reticências '...', complete a frase "
    "somente se o restante do texto estiver visível em outra parte da "
    "imagem; caso contrário, transcreva até onde for legível, sem incluir "
    "as reticências.\n\n"
    "Responda APENAS com o nome do produto transcrito. Sem aspas, sem "
    "explicações, sem markdown."
)


def _encode_image_b64(path: Path) -> tuple[str, str]:
    """Retorna (base64_string, mime_type) para o arquivo de imagem."""
    mime = _MIME_MAP.get(path.suffix.lower(), "image/jpeg")
    with open(path, "rb") as fh:
        return base64.b64encode(fh.read()).decode("utf-8"), mime


def _is_rate_limit(exc: Exception) -> bool:
    """Retorna True se a exceção é um 429 Too Many Requests."""
    if hasattr(exc, 'response') and hasattr(exc.response, 'status_code'):
        return exc.response.status_code == 429
    return '429' in str(exc)


def _rate_limit_error_text(exc: Exception) -> str:
    """Extrai o texto mais informativo possível do erro 429 (corpo JSON da
    resposta da API, quando disponível, senão a mensagem/str da exceção)."""
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        try:
            return json.dumps(body)
        except TypeError:
            pass
    return getattr(exc, "message", None) or str(exc)


def _classify_rate_limit(exc: Exception) -> str:
    """
    Classifica um 429 como:
      "day"     -- limite DIÁRIO (RPD/TPD) -- definitivo até o reset da conta,
                   a chave deve ser descartada pelo resto da execução.
      "minute"  -- limite por MINUTO/segundo (RPM/TPM) -- transitório, só
                   pula para a próxima chave (pode voltar nela depois).
      "unknown" -- não deu para classificar pela mensagem -- tratado como
                   "minute" por segurança (nunca descarta uma chave por engano).
    """
    text = _rate_limit_error_text(exc)

    if _DAY_LIMIT_RE.search(text):
        return "day"
    if _MINUTE_LIMIT_RE.search(text):
        return "minute"

    match = _RETRY_AFTER_RE.search(text)
    if match:
        hours, minutes, seconds = match.groups()
        total = (
            (int(hours) * 3600 if hours else 0)
            + (int(minutes) * 60 if minutes else 0)
            + (float(seconds) if seconds else 0)
        )
        if total > 0:
            return "day" if total > _DAY_LIMIT_THRESHOLD_SECONDS else "minute"

    return "unknown"


class ProductIdentifier:
    """
    Identifica o nome do produto de uma imagem via API Groq multimodal.

    Suporta múltiplas chaves de API (GROQ_API_KEY, GROQ_API_KEY2, GROQ_API_KEY3,
    ...). Quando a chave em uso atinge rate limit (429), o comportamento
    depende da JANELA do limite atingido (ver _classify_rate_limit):

      - Limite por MINUTO (RPM/TPM) -- transitório: alterna imediatamente
        para a próxima chave ainda ativa, sem descartar a atual -- ela volta
        a ser usada normalmente no próximo ciclo. Só aguarda RATE_LIMIT_DELAY
        quando TODAS as chaves ativas estiverem em rate limit por minuto ao
        mesmo tempo.
      - Limite DIÁRIO (RPD/TPD) -- definitivo: a chave é descartada da
        rotação pelo resto da execução (não volta a ser tentada, mesmo que
        outras chaves também esgotem). Quando a última chave ativa esgota o
        limite diário, identify() levanta RuntimeError -- quem chama
        (ProductNamer) já trata isso e cai para o nome padrão P{index}.

    Se nenhuma GROQ_API_KEY* estiver configurada, self.available = False e
    identify() nunca é chamado -- o nome cai de volta para o padrão P{index}.
    """

    def __init__(self) -> None:
        api_keys = _load_api_keys()
        self.available: bool = bool(api_keys)

        if self.available:
            self._clients: list[Groq] = [Groq(api_key=k) for k in api_keys]
            self._key_count: int = len(self._clients)
            self._current_idx: int = 0
            self._exhausted_today: set[int] = set()  # índices de chaves com limite DIÁRIO esgotado
            log.info(
                "Identificação de produto ativa  (modelo: %s, %d chave(s) de API)",
                GROQ_MODEL, self._key_count,
            )
        else:
            log.warning(
                "Nenhuma GROQ_API_KEY configurada -- identificação de produto "
                "desabilitada. Adicione ao menos uma chave no arquivo .env para ativar."
            )

    def _active_indices(self) -> list[int]:
        """Índices de chaves que ainda não esgotaram o limite diário."""
        return [i for i in range(self._key_count) if i not in self._exhausted_today]

    def _next_active_index(self, start_idx: int) -> Optional[int]:
        """Próximo índice ativo em ordem circular a partir de start_idx (exclusive),
        ou None se não sobrar nenhuma chave ativa."""
        for offset in range(1, self._key_count + 1):
            candidate = (start_idx + offset) % self._key_count
            if candidate not in self._exhausted_today:
                return candidate
        return None

    def identify(self, image_path: Path) -> str:
        """
        Chama a API Groq e retorna o nome do produto identificado na imagem.
        Ver docstring da classe para o comportamento de rotação/descarte de
        chaves em rate limit.
        """
        if not self.available:
            raise RuntimeError("Nenhuma GROQ_API_KEY configurada.")

        b64, mime = _encode_image_b64(Path(image_path))
        data_url = f"data:{mime};base64,{b64}"

        error_count = 0
        minute_limited_in_a_row = 0
        rate_limit_wait_cycles = 0
        last_exc: Exception = RuntimeError("Sem tentativas realizadas")

        while error_count < MAX_RETRIES:
            active = self._active_indices()
            if not active:
                raise RuntimeError(
                    f"Todas as {self._key_count} chave(s) GROQ_API_KEY atingiram o "
                    "limite DIÁRIO -- identificação de produto indisponível pelo "
                    "resto da execução."
                ) from last_exc

            if self._current_idx not in active:
                self._current_idx = active[0]

            key_no = self._current_idx + 1
            client = self._clients[self._current_idx]
            try:
                response = client.chat.completions.create(
                    model=GROQ_MODEL,
                    messages=[
                        {
                            "role": "user",
                            "content": [
                                {"type": "text",      "text": _PROMPT},
                                {"type": "image_url", "image_url": {"url": data_url}},
                            ],
                        }
                    ],
                    temperature=0.1,
                    max_tokens=150,
                    reasoning_effort="none",
                )
                name = (response.choices[0].message.content or "").strip()
                name = name.strip('"').strip("'").strip()
                if not name:
                    raise ValueError("Resposta vazia da API")
                return name

            except Exception as exc:
                last_exc = exc
                if not _is_rate_limit(exc):
                    error_count += 1
                    log.warning(
                        "  [WARN] Tentativa %d/%d -- erro na API: %s",
                        error_count, MAX_RETRIES, exc,
                    )
                    if error_count < MAX_RETRIES:
                        time.sleep(RETRY_BASE_DELAY * error_count)
                    continue

                kind = _classify_rate_limit(exc)

                if kind == "day":
                    self._exhausted_today.add(self._current_idx)
                    minute_limited_in_a_row = 0  # composição da rotação mudou -- reinicia a contagem
                    log.warning(
                        "  [...] Chave #%d/%d atingiu o limite DIÁRIO -- descartada "
                        "pelo resto da execução (%d chave(s) ainda ativa(s)).",
                        key_no, self._key_count, len(self._active_indices()),
                    )
                    next_idx = self._next_active_index(self._current_idx)
                    if next_idx is not None:
                        self._current_idx = next_idx
                    continue  # se next_idx for None, o topo do loop levanta o RuntimeError

                # "minute" (ou "unknown", tratado como transitório por segurança):
                # não descarta a chave -- só pula para a próxima ativa, podendo
                # voltar nela em um ciclo futuro.
                minute_limited_in_a_row += 1
                next_idx = self._next_active_index(self._current_idx)
                log.warning(
                    "  [...] Rate limit por minuto (429) na chave #%d/%d -- "
                    "alternando para a chave #%d ...",
                    key_no, self._key_count, (next_idx + 1) if next_idx is not None else key_no,
                )
                if next_idx is not None:
                    self._current_idx = next_idx

                if minute_limited_in_a_row >= len(active):
                    rate_limit_wait_cycles += 1
                    if rate_limit_wait_cycles > MAX_RATE_LIMIT_CYCLES:
                        raise RuntimeError(
                            "Rate limit por minuto persistiu em todas as chaves "
                            f"ativas por {MAX_RATE_LIMIT_CYCLES} ciclos consecutivos."
                        ) from last_exc
                    log.warning(
                        "  [...] Todas as %d chave(s) ativa(s) em rate limit por "
                        "minuto -- aguardando %ds para continuar ...",
                        len(active), int(RATE_LIMIT_DELAY),
                    )
                    time.sleep(RATE_LIMIT_DELAY)
                    minute_limited_in_a_row = 0

        raise RuntimeError(
            f"API falhou após {MAX_RETRIES} tentativas: {last_exc}"
        ) from last_exc
