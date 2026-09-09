"""
Utilitários para interação com FFmpeg e FFprobe via subprocess.
Todas as chamadas são feitas com lista de argumentos (sem shell=True) para
compatibilidade com caminhos que contêm espaços no Windows.
"""

import json
import logging
import os
import shutil
import subprocess
from pathlib import Path
from typing import Tuple

logger = logging.getLogger(__name__)

# Evita que o Windows abra uma janela de console para ffmpeg.exe/ffprobe.exe
# -- sem isso, mesmo com o app em modo janela (sem console próprio), cada
# subprocesso console (ffmpeg é um) recebe uma janela de terminal nova, que
# fica visível até o processo terminar.
_NO_WINDOW = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0


def resolve_ffmpeg_paths(
    base_dir: Path, configured_ffmpeg: str, configured_ffprobe: str
) -> Tuple[str, str]:
    """
    Resolve os caminhos de ffmpeg/ffprobe a usar, com prioridade:
      1. Caminho explícito no config.json (configured_ffmpeg/configured_ffprobe)
         -- não mexe em nada pra quem já tem isso configurado.
      2. Pasta 'ffmpeg/' ao lado do .exe/main.py (base_dir/ffmpeg/ffmpeg.exe),
         usada nos pacotes distribuídos com FFmpeg embutido.
      3. Vazio -- check_ffmpeg() cai para busca no PATH do sistema.
    """
    ffmpeg = configured_ffmpeg
    if not ffmpeg:
        bundled = base_dir / "ffmpeg" / "ffmpeg.exe"
        if bundled.is_file():
            ffmpeg = str(bundled)

    ffprobe = configured_ffprobe
    if not ffprobe:
        bundled = base_dir / "ffmpeg" / "ffprobe.exe"
        if bundled.is_file():
            ffprobe = str(bundled)

    return ffmpeg, ffprobe


def check_ffmpeg(ffmpeg_path: str = "", ffprobe_path: str = "") -> Tuple[str, str]:
    """
    Resolve os executáveis ffmpeg e ffprobe com a seguinte prioridade:
      1. Caminho explícito fornecido (ffmpeg_path / ffprobe_path do config.json)
      2. Busca no PATH do sistema via shutil.which
    """
    if ffmpeg_path:
        if not os.path.isfile(ffmpeg_path):
            raise EnvironmentError(
                f"ffmpeg_path definido no config.json não encontrado: {ffmpeg_path}"
            )
        ffmpeg = ffmpeg_path
        logger.info("FFmpeg (config.json): %s", ffmpeg)
    else:
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            raise EnvironmentError(
                "FFmpeg não encontrado no PATH.\n"
                "Instale em: https://ffmpeg.org/download.html\n"
                "Ou defina 'ffmpeg_path' no config.json."
            )
        logger.info("FFmpeg (PATH): %s", ffmpeg)

    if ffprobe_path:
        if not os.path.isfile(ffprobe_path):
            raise EnvironmentError(
                f"ffprobe_path definido no config.json não encontrado: {ffprobe_path}"
            )
        ffprobe = ffprobe_path
        logger.info("FFprobe (config.json): %s", ffprobe)
    else:
        ffprobe = shutil.which("ffprobe")
        if not ffprobe:
            raise EnvironmentError(
                "FFprobe não encontrado no PATH.\n"
                "FFprobe faz parte do pacote FFmpeg -- reinstale o FFmpeg completo.\n"
                "Ou defina 'ffprobe_path' no config.json."
            )
        logger.info("FFprobe (PATH): %s", ffprobe)

    return ffmpeg, ffprobe


def get_video_duration(video_path: str, ffprobe_path: str = "ffprobe") -> float:
    """
    Retorna a duração em segundos de um vídeo usando FFprobe.
    Tenta primeiro a duração do stream de vídeo, depois a duração do container.
    """
    cmd = [
        ffprobe_path,
        "-v", "quiet",
        "-print_format", "json",
        "-show_format",
        "-show_streams",
        video_path,
    ]

    result = subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=30, creationflags=_NO_WINDOW,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"FFprobe falhou para '{video_path}': {result.stderr.strip()}"
        )

    data = json.loads(result.stdout)

    for stream in data.get("streams", []):
        if stream.get("codec_type") == "video":
            duration = stream.get("duration")
            if duration:
                return float(duration)

    duration = data.get("format", {}).get("duration")
    if duration:
        return float(duration)

    raise RuntimeError(f"Não foi possível determinar a duração de '{video_path}'")


def get_video_dimensions(video_path: str, ffprobe_path: str = "ffprobe") -> Tuple[int, int]:
    """
    Retorna (largura, altura) do primeiro stream de vídeo.
    """
    cmd = [
        ffprobe_path,
        "-v", "quiet",
        "-print_format", "json",
        "-show_streams",
        "-select_streams", "v:0",
        video_path,
    ]

    result = subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=30, creationflags=_NO_WINDOW,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"FFprobe falhou para '{video_path}': {result.stderr.strip()}"
        )

    data = json.loads(result.stdout)
    streams = data.get("streams", [])

    if not streams:
        raise RuntimeError(f"Nenhum stream de vídeo encontrado em '{video_path}'")

    stream = streams[0]
    return int(stream["width"]), int(stream["height"])


def run_ffmpeg(cmd: list, description: str = "", timeout: int = 600) -> None:
    """
    Executa um comando FFmpeg e levanta RuntimeError se falhar.
    O stderr é capturado e registrado em debug; apenas o final é exibido em caso de erro.
    """
    logger.debug("FFmpeg CMD: %s", " ".join(f'"{a}"' if " " in str(a) else str(a) for a in cmd))

    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=_NO_WINDOW,
    )

    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        process.kill()
        raise RuntimeError(f"FFmpeg excedeu o timeout de {timeout}s: {description}")

    if process.returncode != 0:
        tail = stderr[-3000:] if len(stderr) > 3000 else stderr
        logger.debug("FFmpeg stderr completo:\n%s", stderr)
        raise RuntimeError(
            f"FFmpeg falhou (código {process.returncode}) -- {description}\n"
            f"Últimas linhas:\n{tail}"
        )

    logger.debug("FFmpeg concluído: %s", description)
