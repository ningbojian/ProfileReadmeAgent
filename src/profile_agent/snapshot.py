"""快照读写（增量状态）。"""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from .models import BioInfo, Snapshot


def snapshot_text(snap: Snapshot) -> str:
    """快照的磁盘序列化（本地落盘与首次接管 PR 推送共用，必须逐字节一致）。"""
    return json.dumps(asdict(snap), ensure_ascii=False, indent=2) + "\n"


def save_snapshot(path: Path, snap: Snapshot) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(snapshot_text(snap), encoding="utf-8")


def _normalize_repo_readmes(raw: object) -> dict[str, dict]:
    """逐仓条目补缺省（残缺条目不崩溃，测试计划 SB04）。"""
    if not isinstance(raw, dict):
        return {}
    result: dict[str, dict] = {}
    for full_name, entry in raw.items():
        if not isinstance(entry, dict):
            continue
        result[str(full_name)] = {
            "sha": str(entry.get("sha") or ""),
            "block_hash": str(entry.get("block_hash") or ""),
            "mode": str(entry.get("mode") or "block"),
            "failed_hash": str(entry.get("failed_hash") or ""),
            "last_input_hash": str(entry.get("last_input_hash") or ""),
            "pending": bool(entry.get("pending")),
            "updated_at": str(entry.get("updated_at") or ""),
        }
    return result


def _normalize_repo_descriptions(raw: object) -> dict[str, dict]:
    """描述写回条目补缺省；坏条目丢弃（残缺快照不得让进程崩溃）。"""
    if not isinstance(raw, dict):
        return {}
    result: dict[str, dict] = {}
    for full_name, entry in raw.items():
        if not isinstance(entry, dict):
            continue
        try:
            attempts = int(entry.get("attempts") or 0)
        except (TypeError, ValueError):
            attempts = 0
        result[str(full_name)] = {
            "baseline": str(entry.get("baseline") or ""),
            "previous": str(entry.get("previous") or ""),
            "written": str(entry.get("written") or ""),
            "pushed_at": str(entry.get("pushed_at") or ""),
            "generated_at": str(entry.get("generated_at") or ""),
            "manual": bool(entry.get("manual")),
            "attempts": max(0, attempts),
        }
    return result


def load_snapshot(path: Path) -> Snapshot | None:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    if not isinstance(data, dict):
        return None
    bio_raw = data.get("bio") or {}
    if not isinstance(bio_raw, dict):
        bio_raw = {}
    pins_raw = data.get("pins") or []
    if not isinstance(pins_raw, list):
        pins_raw = []
    try:
        return Snapshot(
            # schema 1-4 旧快照读入时自动升级为 5（设计 §5.1 / 描述写回）
            schema=max(5, int(data.get("schema") or 1)),
            username=str(data.get("username") or ""),
            stats_fingerprint=str(data.get("stats_fingerprint") or ""),
            stats_config_hash=str(data.get("stats_config_hash") or ""),
            totals=data.get("totals") or {},
            repos=data.get("repos") or [],
            bio=BioInfo(
                text=str(bio_raw.get("text") or ""),
                text_en=str(bio_raw.get("text_en") or ""),
                input_hash=str(bio_raw.get("input_hash") or ""),
                generated_at=str(bio_raw.get("generated_at") or ""),
                model=str(bio_raw.get("model") or ""),
                prompt_version=int(bio_raw.get("prompt_version") or 0),
                en_dropped_hash=str(bio_raw.get("en_dropped_hash") or ""),
            ),
            repo_readmes=_normalize_repo_readmes(data.get("repo_readmes")),
            repo_descriptions=_normalize_repo_descriptions(
                data.get("repo_descriptions")
            ),
            pins=[str(x) for x in pins_raw],
            readme_last=str(data.get("readme_last") or ""),
            readme_last_en=str(data.get("readme_last_en") or ""),
            generated_at=str(data.get("generated_at") or ""),
        )
    except (TypeError, ValueError):
        return None
