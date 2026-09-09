"""
Gerenciamento de arquivos: descoberta de imagens/vídeos, movimentação e geração de nomes.

Convenção de nomes de saída
===========================
  Cada vídeo gerado recebe o nome  {NomeDoProduto}.mp4
  onde {NomeDoProduto} é o nome completo do produto identificado pela IA
  (ProductIdentifier). A data (dia seguinte ao dia atual) não entra mais no
  nome do arquivo -- só na pasta de saída (output/DD-MM/...).

  Se o nome do produto não estiver disponível (API indisponível ou falha na
  identificação), o nome cai de volta para P{index}.mp4.

  Estrutura de diretórios gerada automaticamente:
    output/
      DD-MM/
        avatar1/
          Fone Bluetooth JBL Preto.mp4
        avatar2/
          Fone Bluetooth JBL Preto.mp4
        ...
    imagem-utilizada/
      DD-MM/
        Fone Bluetooth JBL Preto_DD-MM.jpg
        ...

  Cada avatar tem sua própria subpasta dentro de output/DD-MM/, nomeada a
  partir do arquivo de vídeo do avatar (ex: "avatar1") -- que também é o
  nome usado pela conta do TikTok vinculada a ela (ver tiktok-accounts.json
  no lado Electron: account.folder == nome do arquivo do avatar, sem
  extensão). Um avatar por conta.
"""

import re
import shutil
from collections import OrderedDict
from datetime import date, timedelta
from pathlib import Path
from typing import List, Optional

SUPPORTED_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tiff", ".gif"}
SUPPORTED_VIDEO_EXTENSIONS = {".mp4", ".mov", ".avi", ".mkv", ".webm"}

MAX_PRODUCT_NAME_LENGTH = 80

# Chave especial usada por get_images_by_avatar_folder() para agrupar
# imagens soltas direto no diretório de produtos (sem subpasta de avatar) --
# essas imagens valem para TODOS os avatares, compatibilidade com o layout
# anterior a subpastas por avatar.
LOOSE_IMAGES_KEY = "*"

_INVALID_FS_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED_WINDOWS_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def sanitize_filename(name: str, max_length: int = MAX_PRODUCT_NAME_LENGTH) -> str:
    """
    Converte um nome de produto (texto livre vindo da IA) em um nome de
    arquivo seguro no Windows: remove caracteres inválidos, colapsa espaços
    e limita o tamanho.
    """
    cleaned = _INVALID_FS_CHARS.sub("", name)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    cleaned = cleaned.rstrip(". ")

    if cleaned.upper() in _RESERVED_WINDOWS_NAMES:
        cleaned = f"_{cleaned}"

    if len(cleaned) > max_length:
        cleaned = cleaned[:max_length].rstrip()

    return cleaned or "Produto"


# -- Data ----------------------------------------------------------------------

def get_next_day_label() -> str:
    """Retorna a data de amanhã no formato DD-MM  (ex: '02-06')."""
    tomorrow = date.today() + timedelta(days=1)
    return tomorrow.strftime("%d-%m")


# -- Diretórios ----------------------------------------------------------------

def ensure_directories(dirs: List[str]) -> None:
    for d in dirs:
        Path(d).mkdir(parents=True, exist_ok=True)


# -- Descoberta de arquivos ----------------------------------------------------

def get_background_images(backgrounds_dir: str) -> List[str]:
    path = Path(backgrounds_dir)
    if not path.exists():
        return []

    return [
        str(f)
        for f in sorted(path.iterdir())
        if f.is_file() and f.suffix.lower() in SUPPORTED_IMAGE_EXTENSIONS
    ]


def get_images_by_avatar_folder(images_dir: str) -> "OrderedDict[str, List[str]]":
    """
    Descobre imagens de produto (background/ ou produtos-virais/) agrupadas
    por avatar de destino.

    Cada subpasta direta de images_dir é o nome (stem) do avatar ao qual
    aquelas imagens pertencem exclusivamente -- ex.: background/avatar1/,
    background/avatar2/. Imagens dentro dessas subpastas só devem gerar
    vídeo para o avatar correspondente, não para os demais.

    Imagens soltas direto em images_dir (sem subpasta) ficam agrupadas sob
    a chave especial LOOSE_IMAGES_KEY ("*") e continuam valendo para TODOS
    os avatares -- fallback de compatibilidade com o layout anterior (sem
    subpastas por avatar).

    Retorna um dict ordenado {nome_da_subpasta_ou_LOOSE_IMAGES_KEY: [caminhos]}.
    Não valida se o nome da subpasta corresponde a um avatar existente --
    isso é responsabilidade de quem consome o resultado. Retorna dict vazio
    se images_dir não existir (mesmo comportamento de get_background_images).
    """
    path = Path(images_dir)
    if not path.exists():
        return OrderedDict()

    groups: "OrderedDict[str, List[str]]" = OrderedDict()

    loose = [
        str(f)
        for f in sorted(path.iterdir())
        if f.is_file() and f.suffix.lower() in SUPPORTED_IMAGE_EXTENSIONS
    ]
    if loose:
        groups[LOOSE_IMAGES_KEY] = loose

    for sub in sorted(path.iterdir()):
        if not sub.is_dir() or sub.name.startswith("_"):
            continue
        imgs = [
            str(f)
            for f in sorted(sub.iterdir())
            if f.is_file() and f.suffix.lower() in SUPPORTED_IMAGE_EXTENSIONS
        ]
        if imgs:
            groups[sub.name] = imgs

    return groups


def get_avatar_videos_by_name(avatars_dir: str) -> "OrderedDict[str, str]":
    """
    Descobre os vídeos de avatar, um por conta -- não existe mais o
    conceito de "perfil" com várias variações de um mesmo avatar. Cada
    arquivo de vídeo solto direto em avatars_dir é o avatar de UMA conta,
    identificada pelo nome do arquivo sem extensão (esse nome precisa bater
    com account.folder cadastrado no app -- ver tiktok-accounts.json no
    lado Electron), que por sua vez também nomeia a subpasta de saída
    (output/DD-MM/<nome>/) e a subpasta de produtos dela
    (background/<nome>/).

    Retorna um dict ordenado {nome: caminho_do_vídeo}, ordenado por nome.
    Se dois arquivos tiverem o mesmo nome com extensões diferentes (ex.:
    avatar2.mp4 e avatar2.mov), o último na ordem alfabética de extensão
    vence -- evite deixar mais de um arquivo por nome.

    Lança FileNotFoundError se o diretório não existir ou se nenhum vídeo
    for encontrado.
    """
    path = Path(avatars_dir)
    if not path.exists():
        raise FileNotFoundError(f"Diretório de avatares não encontrado: {avatars_dir}")

    videos: "OrderedDict[str, str]" = OrderedDict()
    for f in sorted(path.iterdir()):
        if f.is_file() and f.suffix.lower() in SUPPORTED_VIDEO_EXTENSIONS:
            videos[f.stem] = str(f)

    if not videos:
        raise FileNotFoundError(
            f"Nenhum vídeo de avatar encontrado em: {avatars_dir}\n"
            f"Formatos aceitos: {', '.join(SUPPORTED_VIDEO_EXTENSIONS)}"
        )

    return videos


# -- Geração de nomes e movimentação ------------------------------------------


def _dedupe_path(dest: Path) -> Path:
    """Se dest já existir, adiciona sufixo _2, _3, ... até achar um livre."""
    if not dest.exists():
        return dest
    counter = 2
    stem = dest.stem
    while dest.exists():
        dest = dest.with_name(f"{stem}_{counter}{dest.suffix}")
        counter += 1
    return dest

def generate_output_filename(
    background_path: str,
    output_dir: str,
    index: int,
    day_label: str,
    avatar_name: str,
    product_name: Optional[str] = None,
) -> str:
    """
    Retorna o caminho completo do vídeo de saída.
    Cria a subpasta output/DD-MM/<avatar_name>/ se ainda não existir.

    Cada avatar grava seus vídeos na própria subpasta (nomeada a partir de
    avatar_name, ex: "avatar1"), independente de quantos perfis/avatares
    existirem. O perfil não entra no path -- ele só determina de qual
    subpasta de avatar/ o vídeo veio.

    Se product_name for informado, ele é sanitizado e usado como nome do
    arquivo (ex: "Fone Bluetooth JBL Preto.mp4"). Caso contrário, cai de
    volta para o padrão P{index} (ex: "P3.mp4"). A data não entra mais no
    nome do arquivo -- só na pasta (output/DD-MM/...).

    Ex.: output/02-06/avatar1/<base>.mp4

    Se o nome resultante já existir (ex: dois produtos com nome muito
    parecido), adiciona sufixo _2, _3, ... para não sobrescrever.
    """
    avatar_dir = sanitize_filename(avatar_name, max_length=60)
    day_dir = Path(output_dir) / day_label / avatar_dir
    day_dir.mkdir(parents=True, exist_ok=True)

    base = sanitize_filename(product_name) if product_name else f"P{index}"
    name = f"{base}.mp4"

    dest = _dedupe_path(day_dir / name)
    return str(dest)


def move_to_used(
    image_path: str,
    used_dir: str,
    index: int,
    day_label: str,
    product_name: Optional[str] = None,
) -> str:
    """
    Move a imagem de fundo para imagem-utilizada/DD-MM/<base>_DD-MM{ext}.
    Cria a subpasta se ainda não existir.
    Se o destino já existir adiciona sufixo _2, _3, ... para não sobrescrever.

    Se product_name for informado, ele é sanitizado e usado como base do
    nome (ex: "Fone Bluetooth JBL Preto_02-06.jpg"). Caso contrário, cai de
    volta para o padrão P{index} (ex: "P3_02-06.jpg").
    """
    src = Path(image_path)
    day_dir = Path(used_dir) / day_label
    day_dir.mkdir(parents=True, exist_ok=True)

    ext  = src.suffix.lower()
    base = sanitize_filename(product_name) if product_name else f"P{index}"
    dest = _dedupe_path(day_dir / f"{base}_{day_label}{ext}")

    shutil.move(str(src), str(dest))
    return str(dest)
