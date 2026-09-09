"""
Carrega e valida o arquivo config.json.
Usa dataclasses para acesso tipado a todas as configurações.
"""

import json
import os
from dataclasses import dataclass, field
from typing import List


@dataclass
class ResolutionConfig:
    width: int
    height: int


@dataclass
class ZoomConfig:
    min_intensity: float
    max_intensity: float


@dataclass
class AvatarConfig:
    scales: List[float]
    base_width_ratio: float


@dataclass
class AvatarPoseConfig:
    """
    Troca de pose (posição + rotação) do avatar DENTRO do mesmo vídeo -- ver
    src/avatar_pose.py. Cicla determinístico pela lista `pose_names`, em
    ordem, um corte a cada `change_interval_seconds` (ex.: padrão
    ["bottom_right","bottom_left"] com intervalo 5s -> direita, esquerda,
    direita, esquerda... a cada 5s). Quando enabled=False, usa só a
    primeira pose da lista do início ao fim do vídeo.
    """
    enabled: bool
    change_interval_seconds: float
    min_segment_seconds: float
    transition_min_seconds: float
    transition_max_seconds: float
    pose_names: List[str]


def _default_avatar_pose() -> "AvatarPoseConfig":
    return AvatarPoseConfig(
        enabled=True,
        change_interval_seconds=5.0,  # troca de pose a cada 5s de vídeo
        min_segment_seconds=4.0,
        transition_min_seconds=0.0,  # corte seco (sem transição suave) -- ver avatar_pose.py
        transition_max_seconds=0.0,
        pose_names=["bottom_right", "bottom_left"],  # vazio == usa todo o registro POSES
    )


@dataclass
class ChromaKeyConfig:
    color: str
    similarity: float
    blend: float


@dataclass
class DirectoriesConfig:
    avatars: str
    backgrounds: str
    used_images: str
    output: str


@dataclass
class AnimationConfig:
    """
    Respiração do produto (zoom + pan) -- ver src/ken_burns.py. A print do
    produto sempre preenche o quadro inteiro (modo "cover"); o zoom apenas
    aproxima/afasta a "câmera", cortando mais ou menos das bordas.
    """
    enabled: bool
    style: str
    min_zoom: float          # quão "encolhida" a respiração deixa a imagem no ponto mínimo (1.0 = sem zoom nenhum)
    pan_intensity: float     # 0..1 -- fração da margem segura disponível usada pra deriva horizontal/vertical
    entry_zoom_seconds: float
    exit_zoom_seconds: float


def _default_animation() -> "AnimationConfig":
    return AnimationConfig(
        enabled=True,
        style="dynamic_tiktok",
        min_zoom=0.80,
        pan_intensity=0.6,
        entry_zoom_seconds=0.5,
        exit_zoom_seconds=0.5,
    )


@dataclass
class PriceLockConfig:
    """
    Bloqueio visual do preço: localiza o preço via OCR (EasyOCR), cobre a
    região com uma barra pixelada/escurecida (assada na imagem, único jeito
    de esconder os dígitos reais do preço) e sobrepõe um GIF animado de
    cadeado pulsando por cima (src/price_badge.py) -- evita problemas de
    "preço enganoso" no TikTok (o valor pode mudar depois que o vídeo foi
    publicado) e gera curiosidade no espectador.
    """
    enabled: bool
    padding_ratio: float
    pixel_block_ratio: float
    overlay_alpha: int
    bar_color: str
    lock_color: str
    ocr_languages: List[str]
    ocr_min_confidence: float
    ocr_width_ths: float
    badge_enabled: bool
    badge_size: int
    badge_fps: int
    badge_loop_seconds: float


def _default_price_lock() -> "PriceLockConfig":
    return PriceLockConfig(
        enabled=True,
        padding_ratio=0.28,
        pixel_block_ratio=0.14,
        overlay_alpha=215,
        bar_color="#FF7A1A",
        lock_color="#FFFFFF",
        ocr_languages=["pt"],
        ocr_min_confidence=0.15,
        ocr_width_ths=0.3,
        badge_enabled=True,
        badge_size=320,
        badge_fps=15,
        badge_loop_seconds=1.6,
    )


@dataclass
class ProductIdentificationConfig:
    """
    Como identificar o nome do produto (usado no nome do arquivo de saída).

    method:
      "ocr"  -- extrai o título direto do print via OCR (src/product_title_ocr.py),
                sem IA/chave de API/internet; cai para o Groq automaticamente
                se o OCR não encontrar o título numa imagem específica.
      "groq" -- usa só a API multimodal do Groq (src/product_identifier.py),
                comportamento anterior.
    """
    method: str = "ocr"


def _default_product_identification() -> "ProductIdentificationConfig":
    return ProductIdentificationConfig(method="ocr")


@dataclass
class DatabaseConfig:
    """Histórico de vídeos gerados, em SQLite."""
    path: str = "history.db"


def _default_database() -> "DatabaseConfig":
    return DatabaseConfig(path="history.db")


@dataclass
class AppConfig:
    resolution: ResolutionConfig
    fps: int
    codec: str
    preset: str
    crf: int
    zoom: ZoomConfig
    avatar: AvatarConfig
    chroma_key: ChromaKeyConfig
    directories: DirectoriesConfig
    animation: AnimationConfig = field(default_factory=_default_animation)
    avatar_pose: AvatarPoseConfig = field(default_factory=_default_avatar_pose)
    price_lock: PriceLockConfig = field(default_factory=_default_price_lock)
    product_identification: ProductIdentificationConfig = field(default_factory=_default_product_identification)
    database: DatabaseConfig = field(default_factory=_default_database)
    video_loops: int = 1
    ffmpeg_path: str = ""
    ffprobe_path: str = ""


def load_config(config_path: str = "config.json") -> AppConfig:
    if not os.path.exists(config_path):
        raise FileNotFoundError(
            f"Arquivo de configuração não encontrado: {config_path}\n"
            f"Certifique-se de executar o script a partir do diretório do projeto."
        )

    with open(config_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    _validate(data)

    return AppConfig(
        resolution=ResolutionConfig(**data["resolution"]),
        fps=int(data["fps"]),
        codec=data["codec"],
        preset=data["preset"],
        crf=int(data["crf"]),
        zoom=ZoomConfig(**data["zoom"]),
        avatar=AvatarConfig(
            scales=[float(s) for s in data["avatar"]["scales"]],
            base_width_ratio=float(data["avatar"]["base_width_ratio"]),
        ),
        chroma_key=ChromaKeyConfig(**data["chroma_key"]),
        directories=DirectoriesConfig(**data["directories"]),
        animation=_parse_animation(data.get("animation", {})),
        avatar_pose=_parse_avatar_pose(data.get("avatar_pose", {})),
        price_lock=_parse_price_lock(data.get("price_lock", {})),
        product_identification=_parse_product_identification(data.get("product_identification", {})),
        database=_parse_database(data.get("database", {})),
        video_loops=int(data.get("video_loops", 1)),
        ffmpeg_path=data.get("ffmpeg_path", ""),
        ffprobe_path=data.get("ffprobe_path", ""),
    )


def _parse_animation(raw: dict) -> AnimationConfig:
    return AnimationConfig(
        enabled=bool(raw.get("enabled", True)),
        style=str(raw.get("style", "dynamic_tiktok")),
        min_zoom=float(raw.get("min_zoom", 0.80)),
        pan_intensity=float(raw.get("pan_intensity", 0.6)),
        entry_zoom_seconds=float(raw.get("entry_zoom_seconds", 0.5)),
        exit_zoom_seconds=float(raw.get("exit_zoom_seconds", 0.5)),
    )


def _parse_price_lock(raw: dict) -> PriceLockConfig:
    return PriceLockConfig(
        enabled=bool(raw.get("enabled", True)),
        padding_ratio=float(raw.get("padding_ratio", 0.28)),
        pixel_block_ratio=float(raw.get("pixel_block_ratio", 0.14)),
        overlay_alpha=int(raw.get("overlay_alpha", 215)),
        bar_color=str(raw.get("bar_color", "#FF7A1A")),
        lock_color=str(raw.get("lock_color", "#FFFFFF")),
        ocr_languages=list(raw.get("ocr_languages", ["pt"])),
        ocr_min_confidence=float(raw.get("ocr_min_confidence", 0.15)),
        ocr_width_ths=float(raw.get("ocr_width_ths", 0.3)),
        badge_enabled=bool(raw.get("badge_enabled", True)),
        badge_size=int(raw.get("badge_size", 320)),
        badge_fps=int(raw.get("badge_fps", 15)),
        badge_loop_seconds=float(raw.get("badge_loop_seconds", 1.6)),
    )


def _parse_product_identification(raw: dict) -> ProductIdentificationConfig:
    method = str(raw.get("method", "ocr"))
    if method not in ("ocr", "groq"):
        raise ValueError(
            f"product_identification.method inválido: {method!r} -- use 'ocr' ou 'groq'"
        )
    return ProductIdentificationConfig(method=method)


def _parse_avatar_pose(raw: dict) -> AvatarPoseConfig:
    return AvatarPoseConfig(
        enabled=bool(raw.get("enabled", True)),
        change_interval_seconds=float(raw.get("change_interval_seconds", 5.0)),
        min_segment_seconds=float(raw.get("min_segment_seconds", 4.0)),
        transition_min_seconds=float(raw.get("transition_min_seconds", 0.0)),
        transition_max_seconds=float(raw.get("transition_max_seconds", 0.0)),
        pose_names=list(raw.get("pose_names", ["bottom_right", "bottom_left"])),
    )


def _parse_database(raw: dict) -> DatabaseConfig:
    return DatabaseConfig(path=str(raw.get("path", "history.db")))


def _validate(data: dict) -> None:
    required = ["resolution", "fps", "codec", "preset", "crf", "zoom",
                "avatar", "chroma_key", "directories"]
    missing = [k for k in required if k not in data]
    if missing:
        raise ValueError(f"Campos obrigatórios ausentes no config: {missing}")

    loops = data.get("video_loops", 1)
    if not isinstance(loops, int) or loops < 1:
        raise ValueError("video_loops deve ser um inteiro >= 1")

    if data["fps"] <= 0:
        raise ValueError("fps deve ser maior que 0")
    if not (0 <= data["crf"] <= 51):
        raise ValueError("crf deve estar entre 0 e 51")
    if not data["avatar"]["scales"]:
        raise ValueError("avatar.scales não pode estar vazio")
