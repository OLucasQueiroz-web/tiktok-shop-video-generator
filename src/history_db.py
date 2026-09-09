"""
Histórico de vídeos gerados (fluxo padrão e viral) em SQLite.

Objetivo: dar visibilidade sobre o que já foi criado (produto, perfil,
avatar, estilo de animação, sucesso/falha) -- não é usado para pular
duplicatas, só para consulta/relatório (`main.py --history`).

Uma falha ao gravar no banco NUNCA deve derrubar a geração de vídeo --
mesma postura defensiva de ProductIdentifier/PriceBlocker.
"""

import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS video_history (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at      TEXT NOT NULL,
    video_type      TEXT NOT NULL CHECK(video_type IN ('standard','viral')),
    day_label       TEXT NOT NULL,
    product_name    TEXT,
    source_image    TEXT NOT NULL,
    profile         TEXT NOT NULL,
    avatar_name     TEXT NOT NULL,
    avatar_path     TEXT NOT NULL,
    animation_style TEXT,
    output_path     TEXT,
    status          TEXT NOT NULL CHECK(status IN ('success','failed')),
    error_message   TEXT
);
CREATE INDEX IF NOT EXISTS idx_video_history_day     ON video_history(day_label);
CREATE INDEX IF NOT EXISTS idx_video_history_type    ON video_history(video_type);
CREATE INDEX IF NOT EXISTS idx_video_history_product ON video_history(product_name);
"""


@dataclass
class VideoRecord:
    video_type: str          # 'standard' | 'viral'
    day_label: str
    source_image: str
    profile: str
    avatar_name: str
    avatar_path: str
    status: str               # 'success' | 'failed'
    product_name: Optional[str] = None
    animation_style: Optional[str] = None
    output_path: Optional[str] = None
    error_message: Optional[str] = None


def init_db(db_path: str) -> None:
    """Cria a tabela/índices se ainda não existirem. Idempotente."""
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.executescript(_SCHEMA)


def record_video(db_path: str, record: VideoRecord) -> None:
    """Grava uma linha de histórico. Nunca lança -- loga warning em falha."""
    try:
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                """
                INSERT INTO video_history (
                    created_at, video_type, day_label, product_name,
                    source_image, profile, avatar_name, avatar_path,
                    animation_style, output_path, status, error_message
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    datetime.now().isoformat(timespec="seconds"),
                    record.video_type,
                    record.day_label,
                    record.product_name,
                    record.source_image,
                    record.profile,
                    record.avatar_name,
                    record.avatar_path,
                    record.animation_style,
                    record.output_path,
                    record.status,
                    record.error_message,
                ),
            )
    except sqlite3.Error as exc:
        logger.warning("Não foi possível gravar histórico no banco: %s", exc)


def fetch_history(
    db_path: str,
    video_type: Optional[str] = None,
    day_label: Optional[str] = None,
    limit: int = 50,
) -> List[sqlite3.Row]:
    """Retorna as linhas mais recentes, filtráveis por tipo e dia."""
    query = "SELECT * FROM video_history WHERE 1=1"
    params: list = []
    if video_type:
        query += " AND video_type = ?"
        params.append(video_type)
    if day_label:
        query += " AND day_label = ?"
        params.append(day_label)
    query += " ORDER BY id DESC LIMIT ?"
    params.append(limit)

    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        return conn.execute(query, params).fetchall()


def summary_counts(db_path: str) -> dict:
    """Totais agregados: geral, por tipo e por status."""
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        total = conn.execute("SELECT COUNT(*) AS n FROM video_history").fetchone()["n"]
        by_type = conn.execute(
            "SELECT video_type, COUNT(*) AS n FROM video_history GROUP BY video_type"
        ).fetchall()
        by_status = conn.execute(
            "SELECT status, COUNT(*) AS n FROM video_history GROUP BY status"
        ).fetchall()

    return {
        "total": total,
        "by_type": {row["video_type"]: row["n"] for row in by_type},
        "by_status": {row["status"]: row["n"] for row in by_status},
    }
