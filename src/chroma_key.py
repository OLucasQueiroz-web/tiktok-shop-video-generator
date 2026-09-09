"""
Gerador da string do filtro chromakey do FFmpeg para remoção de fundo verde.

Decisão técnica: o filtro `chromakey` do FFmpeg opera em espaço de cor YUV,
tornando a detecção de verde puro (#00FF00) muito eficiente.

Parâmetros:
  color       : cor a ser removida em hex RGB
  similarity  : tolerância de cor (0.01-1.0). Valores altos capturam mais tons.
                0.3 funciona bem para verde puro sólido.
  blend       : suavização de borda (0.0-1.0). 0.1 cria transição leve.

Fluxo de conversão necessário:
  O filtro `chromakey` exige entrada em YUVA420P.
  Como vídeos normalmente são YUV420P (sem canal alpha), adicionamos
  `format=yuva420p` antes do chromakey para inserir o canal alpha opaco.
  Após o chromakey, os pixels verdes ficam com alpha=0 (transparente).
"""

from .config_loader import ChromaKeyConfig


def build_chromakey_filter(config: ChromaKeyConfig) -> str:
    """
    Retorna a cadeia de filtros FFmpeg para remoção de fundo verde.
    Inclui a conversão de formato necessária antes do chromakey.
    """
    return (
        f"format=yuva420p,"
        f"chromakey=color={config.color}"
        f":similarity={config.similarity}"
        f":blend={config.blend}"
    )
