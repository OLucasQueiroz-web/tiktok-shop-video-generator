#!/usr/bin/env python3
"""
TikTok Shop Video Generator
=============================================
Gera automaticamente vídeos para TikTok Shop.

Fluxo:
  1. Processa imagens de background/<avatar>/ -> vídeos em output/DD-MM/<avatar>/
  2. O nome do produto é identificado a partir do PRINT em si (ver
     config.product_identification.method e src/product_namer.py) --
     "groq" usa a API multimodal do Groq (modelo Qwen configurado em
     GROQ_MODEL, ver src/product_identifier.py), "ocr" tenta extrair o
     título via OCR primeiro e cai para o Groq se falhar. Se a
     identificação falhar (ou nenhum método estiver disponível), o nome
     cai de volta para o padrão P{index}.
  3. Move imagens processadas para imagem-utilizada/DD-MM/

Um avatar por conta: cada vídeo solto direto em avatar/ (ex: avatar/avatar2.mp4)
é o avatar de UMA conta -- o nome do arquivo (sem extensão) precisa bater
com a pasta de saída daquela conta (ver tiktok-accounts.json no lado
Electron e src/file_manager.py -> get_avatar_videos_by_name()).

Imagens de produto (background/) ficam em subpastas nomeadas igual ao stem
do vídeo do avatar -- background/avatar2/, background/avatar3/... Uma
imagem dentro dessas subpastas só gera vídeo para o avatar correspondente.
Imagens soltas direto em background/ (sem subpasta) são ignoradas (com
aviso) -- todo produto precisa estar vinculado a um avatar/conta.

Todo vídeo gerado é registrado num histórico SQLite (config.database.path)
-- consulte com `python main.py --history`.

Geração é sequencial por avatar: todas as imagens de um avatar são geradas
antes de passar pro próximo. Com --auto-post, a postagem só começa depois
que TODOS os avatares já tiverem gerado seus vídeos -- geração e postagem
não ficam mais intercaladas.

Uso:
  python main.py                    # Processamento normal
  python main.py --dry-run          # Lista arquivos sem processar
  python main.py --config outro.json
  python main.py -v                 # Modo verbose
  python main.py --history          # Mostra o histórico de vídeos gerados

Saída:
  output/<DD-MM>/<avatar>/<Nome Completo do Produto>_<DD-MM>.mp4

  O nome do vídeo vem do nome do produto identificado a partir do próprio
  print (ver acima) -- só cai de volta para o padrão P1, P2... se a
  identificação falhar ou não houver nenhum método disponível.
"""

import subprocess
import sys

# Garante UTF-8 no stdout/stderr independente do locale do Windows (cp1252/cp850).
# Deve rodar antes de qualquer import que escreva no console.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import argparse
import json
import logging
import os

def _prog(msg: str) -> None:
    """Emite evento de progresso estruturado para o frontend Electron."""
    print(f"__PROG__:{msg}", flush=True)
import time
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

from src.config_loader import AppConfig, load_config
from src.ocr_reader import create_reader
from src.price_blocker import PriceBlocker
from src.file_manager import (
    LOOSE_IMAGES_KEY,
    ensure_directories,
    generate_output_filename,
    get_avatar_videos_by_name,
    get_images_by_avatar_folder,
    get_next_day_label,
    move_to_used,
)
from src.ffmpeg_utils import check_ffmpeg, get_video_duration, resolve_ffmpeg_paths
from src.video_processor import process_video
from src.history_db import VideoRecord, fetch_history, init_db, record_video, summary_counts
from src.product_namer import ProductNamer


def _resolve_base_dir() -> Path:
    """
    Diretório-base da aplicação: pasta do .exe quando "congelado" (PyInstaller),
    ou pasta do próprio main.py quando rodando a partir do código-fonte.
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent


def has_api_key(base_dir: Path) -> bool:
    """Carrega o .env ao lado do .exe/main.py e diz se GROQ_API_KEY já está configurada."""
    load_dotenv(dotenv_path=base_dir / ".env", override=True)
    return bool(os.environ.get("GROQ_API_KEY"))


def save_api_key(base_dir: Path, key: str) -> None:
    """Grava GROQ_API_KEY no .env (ao lado do .exe/main.py) e recarrega o ambiente."""
    env_path = base_dir / ".env"
    with open(env_path, "a", encoding="utf-8") as f:
        f.write(f"\nGROQ_API_KEY={key}\n")
    load_dotenv(dotenv_path=env_path, override=True)


def _setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)-7s] %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stdout,
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="TikTok Shop Video Generator",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--config", "-c",
        default="config.json",
        help="Caminho para o arquivo de configuração (padrão: config.json)",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Ativa logging detalhado (debug)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Lista os pares background->avatar sem gerar vídeos",
    )
    parser.add_argument(
        "--history",
        action="store_true",
        help="Mostra o histórico de vídeos já gerados (banco SQLite) e sai",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Com --history, imprime em JSON em vez de tabela (consumido pelo frontend Electron)",
    )
    parser.add_argument(
        "--history-day",
        default=None,
        help="Filtra o histórico por dia de publicação (formato DD-MM)",
    )
    parser.add_argument(
        "--history-type",
        choices=["standard", "viral"],
        default=None,
        help="Filtra o histórico por tipo de vídeo (\"viral\" só existe em registros antigos)",
    )
    parser.add_argument(
        "--auto-post",
        dest="auto_post",
        action="store_true",
        default=True,
        help=(
            "Posta os vídeos no TikTok Studio (electron/tiktok_uploader.js) depois que TODOS "
            "já tiverem sido gerados (de todos os avatares) -- geração termina por completo "
            "antes da postagem começar. Publica de verdade (a menos que --auto-post-dry-run "
            "também seja passado). LIGADO por padrão -- use --no-auto-post pra desligar."
        ),
    )
    parser.add_argument(
        "--no-auto-post",
        dest="auto_post",
        action="store_false",
        help="Desliga o auto-post -- só gera os vídeos, sem postar no TikTok.",
    )
    parser.add_argument(
        "--auto-post-dry-run",
        action="store_true",
        help="Com --auto-post, roda o uploader em modo dry-run (não publica, só valida o fluxo).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Processa só as N primeiras imagens pendentes do fluxo padrão (útil pra testar em lote pequeno).",
    )
    return parser.parse_args()


def _print_history(config, day_label: Optional[str], video_type: Optional[str], as_json: bool = False) -> int:
    """Imprime o histórico de vídeos gerados (tabela simples ou JSON) e um resumo."""
    rows = fetch_history(config.database.path, video_type=video_type, day_label=day_label)
    counts = summary_counts(config.database.path)

    if as_json:
        # Prefixo __HISTORY_JSON__: (mesma convenção do __PROG__: usado em
        # run_pipeline) para o frontend Electron achar a linha de dados em
        # meio aos logs de inicialização que já vão pro stdout antes daqui.
        payload = json.dumps({
            "rows": [dict(row) for row in rows],
            "counts": counts,
        }, ensure_ascii=False)
        print(f"__HISTORY_JSON__:{payload}", flush=True)
        return 0

    if not rows:
        print("Nenhum registro de histórico encontrado.")
    else:
        header = f"{'#':<5} {'data':<8} {'tipo':<10} {'avatar':<12} {'estilo':<20} {'status':<8} produto"
        print(header)
        print("-" * len(header))
        for row in rows:
            print(
                f"{row['id']:<5} {row['day_label']:<8} {row['video_type']:<10} "
                f"{row['avatar_name']:<12} "
                f"{(row['animation_style'] or '-'):<20} {row['status']:<8} "
                f"{row['product_name'] or '-'}"
            )

    print()
    print(f"Total: {counts['total']}  |  por tipo: {counts['by_type']}  |  por status: {counts['by_status']}")
    return 0


def _pair_images_with_avatars(
    images_dir: str,
    flat_avatars: list,
    logger: logging.Logger,
) -> list:
    """
    Descobre as imagens em images_dir (background/) e as associa ao avatar
    (conta) certo, respeitando a convenção de subpastas de
    get_images_by_avatar_folder() (ver src/file_manager.py): images_dir/
    avatar2/*, images_dir/avatar3/*, ... -> a imagem só gera vídeo para o
    avatar cujo nome (stem do arquivo de vídeo) bate com o nome da
    subpasta. Subpasta sem avatar correspondente é ignorada (com aviso).

    Imagens soltas direto em images_dir (sem subpasta) não têm mais um
    avatar implícito -- são ignoradas com aviso (todo produto precisa
    pertencer a um avatar/conta específico).

    Retorna uma lista de pares (caminho_da_imagem, [(nome_avatar, avatar_path)]),
    uma entrada por imagem, na ordem de descoberta -- o segundo elemento é
    sempre uma lista de 1 item (não existe mais avatar "compartilhado").
    """
    avatar_by_name: dict = {name: (name, path) for name, path in flat_avatars}

    groups = get_images_by_avatar_folder(images_dir)
    pairs: list = []
    for group_name, imgs in groups.items():
        if group_name == LOOSE_IMAGES_KEY:
            logger.warning(
                "%d imagem(ns) solta(s) direto em '%s' (sem subpasta de avatar) -- ignorada(s). "
                "Toda imagem precisa estar em uma subpasta com o nome do avatar/conta.",
                len(imgs), images_dir,
            )
            continue
        target = avatar_by_name.get(group_name)
        if not target:
            logger.warning(
                "Pasta '%s' em '%s' não corresponde a nenhum avatar conhecido -- ignorada.",
                group_name, images_dir,
            )
            continue
        for img in imgs:
            pairs.append((img, [target]))
    return pairs


def _identify_product_name_and_band(
    product_namer: ProductNamer,
    image_path: str,
    logger: logging.Logger,
):
    """
    Identifica o nome do produto a partir do print, via ProductNamer (OCR
    e/ou Groq, conforme config.product_identification.method), e também
    retorna a caixa (x1,y1,x2,y2, pixels da imagem ORIGINAL) da zona do
    título -- usada pra manter o avatar de tampar o nome do produto no
    vídeo gerado (ver src/avatar_pose.py e src/video_processor.py). Nunca
    propaga exceção -- ProductNamer já captura erros dos seus próprios
    métodos; aqui é só uma rede de segurança extra. Retorna (None, None) se
    a identificação falhar ou a caixa não puder ser localizada (ex.: nome
    via Groq, sem OCR) -- quem chama trata nome=None como "usa o nome
    padrão P{index}" e banda=None como "avatar na posição padrão".
    """
    try:
        return product_namer.identify_with_band(image_path)
    except Exception as exc:
        logger.warning("  Falha ao identificar nome do produto: %s", exc)
        return None, None


def _post_video(output_path: str, avatar_name: str, logger: logging.Logger, dry_run: bool = False) -> bool:
    """
    Chama o uploader Node (electron/tiktok_uploader.js) em modo arquivo único
    pra postar UM vídeo específico no TikTok Studio, e espera terminar antes
    de devolver o controle -- usado por --auto-post pra intercalar geração e
    postagem (gera 1 -> posta 1 -> gera o próximo).

    Roda com o Chrome visível (o uploader não suporta headless -- ver
    launchBrowser em tiktok_uploader.js) e pode levar alguns minutos por
    vídeo (upload + checagens do TikTok). Retorna True se o uploader saiu
    com código 0.

    Intérprete: por padrão usaria "node" do PATH, mas o instalador
    autossuficiente embute o próprio Node.js (baseDir/node/node.exe -- ver
    installer-assets/node/ e resolveNodeExe() em electron/main.js) já que
    não garante um instalado na máquina de quem recebe o app (só o app em
    si e o Chrome são pré-requisitos). O Electron (electron/pipeline.js)
    passa TIKTOK_NODE_EXE (esse binário bundled) e TIKTOK_ELECTRON_DIR
    (onde fica tiktok_uploader.js) por variável de ambiente. Sem essas
    variáveis (main.py rodado direto, fora do Electron, ex.: testes
    manuais), cai de volta pro "node" do PATH e pro caminho relativo a
    este arquivo -- mesmo comportamento de antes.
    """
    electron_dir_env = os.environ.get("TIKTOK_ELECTRON_DIR")
    electron_dir = Path(electron_dir_env) if electron_dir_env else Path(__file__).resolve().parent / "electron"
    node_script = electron_dir / "tiktok_uploader.js"

    node_exe = os.environ.get("TIKTOK_NODE_EXE") or "node"
    cmd = [node_exe, str(node_script), "--file", output_path, avatar_name]
    if dry_run:
        cmd.append("--dry-run")

    logger.info("  Postando no TikTok (%s)...", avatar_name)
    try:
        result = subprocess.run(cmd, cwd=str(electron_dir.parent))
    except FileNotFoundError:
        logger.error("  Não consegui rodar o uploader -- '%s' não encontrado.", node_exe)
        return False

    if result.returncode != 0:
        logger.error("  Uploader terminou com erro (código %d) pra %s.", result.returncode, Path(output_path).name)
        return False

    logger.info("  Postado -> %s", Path(output_path).name)
    return True


def _run_standard_flow(
    config: AppConfig,
    price_blocker: PriceBlocker,
    product_namer: ProductNamer,
    image_avatar_pairs: list,
    day_label: str,
    ffmpeg_path: str,
    ffprobe_path: str,
    price_lock_cache_dir: Path,
    assets_dir: Path,
    logger: logging.Logger,
    auto_post: bool = False,
    auto_post_dry_run: bool = False,
) -> tuple:
    """
    Para cada imagem em config.directories.backgrounds, gera o vídeo do
    avatar já filtrado por _pair_images_with_avatars() conforme a subpasta
    de origem da imagem (um avatar por conta -- não existe mais imagem
    compartilhada entre avatares). O nome do produto é identificado a
    partir do próprio print (product_namer.identify()), com fallback pro
    padrão P{index} se a identificação falhar. Retorna (success, failed,
    failed_files, n_total).

    Com auto_post=True, a postagem só começa depois que TODOS os vídeos (de
    todos os avatares) já foram gerados -- não intercala mais geração com
    postagem vídeo a vídeo. A ordem de postagem segue a mesma ordem de
    geração (um avatar por completo antes do próximo).
    """
    n_total = sum(len(targets) for _, targets in image_avatar_pairs)
    _prog(f"INIT:{n_total}:{len(image_avatar_pairs)}")

    success = 0
    failed  = 0
    failed_files: list = []
    video_count = 0
    to_post: list[tuple[str, str]] = []  # (output_path, avatar_name) dos vídeos gerados com sucesso

    logger.info(
        "Gerando %d vídeo(s) a partir de %d imagem(ns)",
        n_total, len(image_avatar_pairs),
    )

    for i, (bg_path, targets) in enumerate(image_avatar_pairs, 1):
        bg_name   = Path(bg_path).name
        any_ok    = False
        interrupted = False

        # -- Nome do produto identificado a partir do próprio print -- usado --
        # na nomeação do(s) vídeo(s) e da imagem movida (fallback: P{index}).
        # title_band (x1,y1,x2,y2 na imagem ORIGINAL, só via OCR) mantém o
        # avatar de tampar o título no vídeo -- ver video_processor.py.
        product_name, title_band = _identify_product_name_and_band(product_namer, bg_path, logger)
        if product_name:
            logger.info("  Produto identificado: %s", product_name)
        else:
            logger.info("  Produto não identificado -- usando nome padrão P%d.", i)

        # -- Bloqueia o preço (uma vez por produto) -- se não encontrar preço --
        # ou o OCR estiver indisponível, cai de volta para a imagem original.
        lock_result = price_blocker.apply_lock(bg_path, config.price_lock, price_lock_cache_dir)
        video_bg_path = lock_result.image_path if lock_result else bg_path

        for avatar_name, avatar_path in targets:
            video_count += 1
            output_path = generate_output_filename(
                bg_path,
                config.directories.output,
                index=i,
                day_label=day_label,
                avatar_name=avatar_name,
                product_name=product_name,
            )
            out_name = Path(output_path).name

            logger.info(
                "[%d/%d] P%d  %s + %s -> %s",
                video_count, n_total, i, bg_name, avatar_name, out_name,
            )

            _prog(f"VIDEO_START:{video_count}:{n_total}:{bg_name}")
            try:
                duration = get_video_duration(avatar_path, ffprobe_path)
                process_video(
                    background_path=video_bg_path,
                    avatar_path=avatar_path,
                    output_path=output_path,
                    duration=duration,
                    config=config,
                    ffmpeg_path=ffmpeg_path,
                    ffprobe_path=ffprobe_path,
                    price_lock_result=lock_result,
                    assets_dir=assets_dir,
                    title_band=title_band,
                )
                any_ok = True
                success += 1
                _prog(f"VIDEO_DONE:{video_count}:{n_total}")
                logger.info("  OK -> %s", out_name)
                record_video(config.database.path, VideoRecord(
                    video_type="standard", day_label=day_label,
                    source_image=bg_path, profile=avatar_name,
                    avatar_name=avatar_name, avatar_path=avatar_path,
                    product_name=product_name, output_path=output_path,
                    status="success",
                ))

                if auto_post:
                    to_post.append((output_path, avatar_name))

            except KeyboardInterrupt:
                logger.warning("Interrompido pelo usuário.")
                _prog(f"VIDEO_FAIL:{video_count}:{n_total}")
                interrupted = True
                break

            except Exception as e:
                logger.error("  FALHOU (video): %s", e)
                failed += 1
                failed_files.append(f"{bg_name} ({avatar_name})")
                _prog(f"VIDEO_FAIL:{video_count}:{n_total}")
                record_video(config.database.path, VideoRecord(
                    video_type="standard", day_label=day_label,
                    source_image=bg_path, profile=avatar_name,
                    avatar_name=avatar_name, avatar_path=avatar_path,
                    product_name=product_name, status="failed",
                    error_message=str(e),
                ))

        # -- Limpa a imagem temporária com preço bloqueado (já usada em todos --
        # os avatares deste produto; a original em bg_path segue intacta).
        if lock_result:
            try:
                Path(lock_result.image_path).unlink(missing_ok=True)
            except OSError as exc:
                logger.debug("  Não foi possível remover cache de preço bloqueado: %s", exc)

        if interrupted:
            break

        # -- Move imagem após pelo menos 1 avatar gerar com sucesso ------------
        if any_ok:
            used_path = move_to_used(
                bg_path, config.directories.used_images, index=i, day_label=day_label,
                product_name=product_name,
            )
            logger.debug("  Imagem movida -> %s", used_path)

    # -- Postagem: só começa depois que TODOS os vídeos já foram gerados -----
    # (todos os avatares) -- ver docstring. A ordem de to_post já segue a
    # mesma ordem de geração (um avatar por completo antes do próximo).
    if auto_post and to_post:
        logger.info("=" * 55)
        logger.info("  Gerado tudo -- iniciando postagem de %d vídeo(s)", len(to_post))
        logger.info("=" * 55)
        for post_index, (output_path, avatar_name) in enumerate(to_post, 1):
            logger.info("[postagem %d/%d] %s", post_index, len(to_post), Path(output_path).name)
            _post_video(output_path, avatar_name, logger, dry_run=auto_post_dry_run)

    return success, failed, failed_files, n_total


def run_pipeline(
    config_path: str,
    dry_run: bool,
    logger: logging.Logger,
    auto_post: bool = False,
    auto_post_dry_run: bool = False,
    limit: Optional[int] = None,
) -> tuple:
    """
    Orquestra um ciclo completo: carrega config, resolve FFmpeg, descobre
    avatares/imagens e roda o fluxo de geração. Usada tanto pelo CLI
    (main()) quanto pelo Electron (pipeline.js) -- toda a lógica de "o que
    rodar e em que ordem" mora aqui uma única vez.

    Um avatar por conta -- as imagens de cada avatar são geradas por
    completo antes de passar pro próximo, na ordem alfabética do nome do
    avatar (ver _pair_images_with_avatars() e get_images_by_avatar_folder()).
    Com auto_post=True, a postagem de todos os vídeos só começa depois que
    TODOS os avatares já tiverem terminado de gerar -- ver _run_standard_flow().

    Retorna (success, failed) (em modo dry_run sempre retorna (0, 0), já
    que nada é gerado de fato).

    Propaga as mesmas exceções que load_config/check_ffmpeg/get_avatar_videos_by_name
    (FileNotFoundError, ValueError, EnvironmentError) -- quem chama decide
    como reportar (CLI: logger.error + return 1; GUI: messagebox).
    """
    base_dir = _resolve_base_dir()

    # -- Carrega o .env ao lado do .exe/main.py -- necessário mesmo quando --
    # rodando como executável empacotado (Electron chama o CLI num processo
    # separado, que não herda load_dotenv() de nenhum outro lugar) para que
    # GROQ_API_KEY* fiquem disponíveis para o ProductIdentifier abaixo.
    load_dotenv(dotenv_path=base_dir / ".env", override=True)

    config = load_config(config_path)

    day_label = get_next_day_label()
    logger.info("  Data de publicação : %s", day_label)

    # -- Reader OCR compartilhado (ver src/ocr_reader.py) -- usado pelo --
    # bloqueio de preço e, se config.product_identification.method == "ocr",
    # também pela identificação de título via OCR (ver src/product_namer.py).
    ocr_reader = create_reader(config.price_lock.ocr_languages) if config.price_lock.enabled else None

    price_blocker = PriceBlocker(enabled=config.price_lock.enabled, reader=ocr_reader)

    # -- Identificação do nome do produto a partir do print (ver docstring --
    # do módulo e src/product_namer.py). Instanciado uma única vez por
    # execução para reaproveitar os clientes Groq e o estado de chaves com
    # limite diário esgotado entre todas as imagens processadas.
    product_namer = ProductNamer(config.product_identification.method, reader=ocr_reader)
    price_lock_cache_dir = Path(config.directories.output) / "_price_lock_cache"
    assets_dir = base_dir / "assets"  # cache do GIF do cadeado (ver src/price_badge.py)

    out_subdir  = str(Path(config.directories.output) / day_label)
    used_subdir = str(Path(config.directories.used_images) / day_label)

    # -- FFmpeg -- prioridade: config.json explícito -> pasta ffmpeg/ ao lado do
    # .exe -> PATH do sistema.
    ffmpeg_configured, ffprobe_configured = resolve_ffmpeg_paths(
        base_dir, config.ffmpeg_path, config.ffprobe_path
    )
    ffmpeg_path, ffprobe_path = check_ffmpeg(
        ffmpeg_path=ffmpeg_configured,
        ffprobe_path=ffprobe_configured,
    )

    ensure_directories([
        config.directories.output,
        config.directories.used_images,
        config.directories.avatars,
        config.directories.backgrounds,
    ])

    logger.info("  Saída       : %s", out_subdir)
    logger.info("  Utilizadas  : %s", used_subdir)

    init_db(config.database.path)

    avatar_videos = get_avatar_videos_by_name(config.directories.avatars)
    flat_avatars = list(avatar_videos.items())

    # -- Imagens de produto associadas ao avatar certo (ver _pair_images_with_ --
    # avatars() e get_images_by_avatar_folder() em src/file_manager.py):
    # background/avatar2/, background/avatar3/... restringem a imagem ao
    # avatar correspondente -- um avatar por conta, sem imagem compartilhada.
    background_pairs = _pair_images_with_avatars(config.directories.backgrounds, flat_avatars, logger)
    if limit is not None:
        background_pairs = background_pairs[:limit]
        logger.info("  --limit %d: processando só as %d primeira(s) imagem(ns) de %s.", limit, len(background_pairs), config.directories.backgrounds)

    logger.info(
        "Encontrados: %d imagem(ns) de produto | %d avatar(es)",
        len(background_pairs), len(flat_avatars),
    )

    if not background_pairs:
        logger.warning("Nenhuma imagem de produto encontrada em '%s'.", config.directories.backgrounds)

    # -- Dry-run ----------------------------------------------------------------
    if dry_run:
        if background_pairs:
            n_total_dry = sum(len(targets) for _, targets in background_pairs)
            logger.info("=== MODO DRY-RUN (nenhum vídeo será gerado) ===")
            logger.info(
                "%d imagem(ns) -> %d vídeo(s)",
                len(background_pairs), n_total_dry,
            )
            logger.info(
                "  (nomes abaixo usam o nome do arquivo como prévia -- a "
                "execução real identifica o nome do produto via %s)",
                config.product_identification.method,
            )
            count = 0
            for i, (bg, targets) in enumerate(background_pairs, 1):
                for avatar_name, av in targets:
                    count += 1
                    out = generate_output_filename(
                        bg, config.directories.output,
                        index=i, day_label=day_label,
                        avatar_name=avatar_name,
                        product_name=Path(bg).stem,
                    )
                    logger.info(
                        "  [%d/%d] %-30s + %-20s -> %s",
                        count, n_total_dry,
                        Path(bg).name, avatar_name, Path(out).name,
                    )
        return 0, 0

    # -- Processamento principal ------------------------------------------------
    start_time = time.time()
    success = failed = 0
    failed_files: list[str] = []

    if background_pairs:
        success, failed, failed_files, _ = _run_standard_flow(
            config, price_blocker, product_namer, background_pairs, day_label,
            ffmpeg_path, ffprobe_path, price_lock_cache_dir, assets_dir, logger,
            auto_post=auto_post, auto_post_dry_run=auto_post_dry_run,
        )

    # -- Resumo -----------------------------------------------------------------
    elapsed = time.time() - start_time
    avg     = elapsed / success if success > 0 else 0

    logger.info("=" * 55)
    logger.info("  Processados : %d", success + failed)
    logger.info("  Vídeos OK   : %d", success)
    logger.info("  Falhas      : %d", failed)
    logger.info("  Tempo total : %.1fs (%.1fs/vídeo)", elapsed, avg)
    logger.info("  Saída       : %s", Path(out_subdir).absolute())

    if failed_files:
        logger.warning("Arquivos com falha:")
        for f in failed_files:
            logger.warning("  - %s", f)

    _prog("ALL_DONE")
    return success, failed


def main() -> int:
    base_dir = _resolve_base_dir()
    if getattr(sys, "frozen", False):
        os.chdir(base_dir)  # garante que todos os caminhos relativos (config.json,
                             # avatar/, background/, history.db...) resolvem certo
                             # não importa de onde o .exe foi clicado.

    args = _parse_args()
    _setup_logging(args.verbose)
    logger = logging.getLogger(__name__)

    logger.info("=" * 55)
    logger.info("  TikTok Shop -- Vídeo Generator")
    logger.info("=" * 55)

    # -- Configuração -----------------------------------------------------------
    try:
        config = load_config(args.config)
    except (FileNotFoundError, ValueError) as e:
        logger.error("Erro ao carregar config: %s", e)
        return 1

    # -- Histórico: modo somente leitura, não precisa de FFmpeg/diretórios ------
    if args.history:
        return _print_history(config, day_label=args.history_day, video_type=args.history_type, as_json=args.json)

    try:
        success, failed = run_pipeline(
            args.config, args.dry_run, logger,
            auto_post=args.auto_post, auto_post_dry_run=args.auto_post_dry_run,
            limit=args.limit,
        )
    except (FileNotFoundError, ValueError, EnvironmentError) as e:
        logger.error("%s", e)
        return 1

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
