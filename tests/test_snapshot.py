"""snapshot.py 读写测试。"""

import json
from pathlib import Path

from profile_agent.models import BioInfo, Snapshot
from profile_agent.snapshot import load_snapshot, save_snapshot, snapshot_text


def _sample() -> Snapshot:
    return Snapshot(
        schema=1,
        username="alice",
        stats_fingerprint="sha256:abc",
        totals={"public_repos": 42, "followers": 128, "total_stars": 230},
        repos=[{"full_name": "alice/cool-tool", "stars": 120}],
        bio=BioInfo(
            text="你好，我是 Alice。",
            input_hash="sha256:def",
            generated_at="2026-10-21T02:00:00+00:00",
            model="claude-opus-5-5",
            prompt_version=1,
        ),
        generated_at="2026-10-21T02:00:00+00:00",
    )


def test_roundtrip(tmp_path):
    path = tmp_path / "snapshot.json"
    save_snapshot(path, _sample())
    loaded = load_snapshot(path)
    assert loaded is not None
    assert loaded.username == "alice"
    assert loaded.stats_fingerprint == "sha256:abc"
    assert loaded.totals["total_stars"] == 230
    assert loaded.repos[0]["full_name"] == "alice/cool-tool"
    assert loaded.bio.text == "你好，我是 Alice。"
    assert loaded.bio.prompt_version == 1


def test_load_missing_returns_none(tmp_path):
    assert load_snapshot(tmp_path / "none.json") is None


def test_load_corrupt_returns_none(tmp_path):
    path = tmp_path / "snapshot.json"
    path.write_text("{not json", encoding="utf-8")
    assert load_snapshot(path) is None


def test_creates_parent_dirs(tmp_path):
    path = tmp_path / "data" / "nested" / "snapshot.json"
    save_snapshot(path, _sample())
    assert path.exists()


def test_roundtrip_includes_config_hash(tmp_path):
    snap = _sample()
    snap.stats_config_hash = "sha256:cfg"
    path = tmp_path / "s.json"
    save_snapshot(path, snap)
    loaded = load_snapshot(path)
    assert loaded is not None
    assert loaded.stats_config_hash == "sha256:cfg"


def test_load_non_dict_json_returns_none(tmp_path):
    path = tmp_path / "s.json"
    path.write_text("[]", encoding="utf-8")
    assert load_snapshot(path) is None


def test_snapshot_text_is_exactly_what_save_snapshot_writes(tmp_path):
    """首次接管 PR 要按仓库相对路径推送快照——序列化必须与本地落盘逐字节一致。"""
    path = tmp_path / "s.json"
    snap = _sample()
    save_snapshot(path, snap)
    assert path.read_text(encoding="utf-8") == snapshot_text(snap)
    assert snapshot_text(snap).endswith("\n")


# ---- schema 3 与 repo_readmes（模式 B，测试计划 SB01-SB04）----


def test_schema2_upgrades_to_3_with_empty_repo_readmes(tmp_path):
    """SB01：schema 2 旧快照读入自动升级并补空 repo_readmes，旧字段原样保留。

    有意变更（schema 5 仓库描述写回）：升级目标由 4 改为 5。
    """
    path = tmp_path / "s.json"
    path.write_text(
        json.dumps(
            {
                "schema": 2,
                "username": "alice",
                "stats_fingerprint": "sha256:abc",
                "bio": {"text": "旧简介", "input_hash": "sha256:def"},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    loaded = load_snapshot(path)
    assert loaded is not None
    assert loaded.schema == 5
    assert loaded.repo_readmes == {}
    assert loaded.username == "alice"
    assert loaded.bio.text == "旧简介"
    # 二次往返后仍 schema=5 且含 "repo_readmes": {}
    save_snapshot(path, loaded)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["schema"] == 5
    assert data["repo_readmes"] == {}


def test_schema1_upgrades_to_3(tmp_path):
    """SB02：schema 1 快照同样兼容读入。

    有意变更（schema 5 仓库描述写回）：升级目标由 4 改为 5。
    """
    path = tmp_path / "s.json"
    path.write_text(
        json.dumps({"schema": 1, "username": "alice", "stats_fingerprint": "sha256:abc"}),
        encoding="utf-8",
    )
    loaded = load_snapshot(path)
    assert loaded is not None
    assert loaded.schema == 5
    assert loaded.repo_readmes == {}
    assert loaded.stats_fingerprint == "sha256:abc"


def test_repo_readmes_full_field_roundtrip(tmp_path):
    """SB03：repo_readmes 全字段往返（含 last_input_hash 与 pending，逐仓独立）。"""
    snap = _sample()
    snap.repo_readmes = {
        "alice/cool-tool": {
            "sha": "abc123",
            "block_hash": "sha256:b1",
            "mode": "block",
            "failed_hash": "",
            "last_input_hash": "sha256:i1",
            "pending": False,
            "updated_at": "2026-10-22T00:00:00+00:00",
        },
        "alice/old-thing": {
            "sha": "def456",
            "block_hash": "",
            "mode": "full",
            "failed_hash": "sha256:f1",
            "last_input_hash": "",
            "pending": True,
            "updated_at": "2026-10-22T01:00:00+00:00",
        },
    }
    path = tmp_path / "s.json"
    save_snapshot(path, snap)
    loaded = load_snapshot(path)
    assert loaded is not None
    cool = loaded.repo_readmes["alice/cool-tool"]
    assert cool["sha"] == "abc123"
    assert cool["mode"] == "block"
    assert cool["last_input_hash"] == "sha256:i1"
    assert cool["pending"] is False
    old = loaded.repo_readmes["alice/old-thing"]
    assert old["mode"] == "full"
    assert old["failed_hash"] == "sha256:f1"
    assert old["pending"] is True


def test_malformed_repo_readme_entry_fills_defaults(tmp_path):
    """SB04：残缺条目读入补缺省不崩溃。"""
    path = tmp_path / "s.json"
    path.write_text(
        json.dumps(
            {
                "schema": 3,
                "username": "alice",
                "repo_readmes": {"alice/x": {"sha": "abc", "updated_at": "t"}},
            }
        ),
        encoding="utf-8",
    )
    loaded = load_snapshot(path)
    assert loaded is not None
    entry = loaded.repo_readmes["alice/x"]
    assert entry["sha"] == "abc"
    assert entry["pending"] is False
    assert entry["last_input_hash"] == ""
    assert entry["failed_hash"] == ""
    assert entry["mode"] == "block"


def test_snapshot_schema5_roundtrip_pins_bio_en_and_descriptions(tmp_path):
    """schema 5：pins、bio.text_en、readme_last、repo_descriptions 保存后读回一致。"""
    snap = Snapshot(
        schema=5,
        username="alice",
        pins=["alice/cool-tool", "alice/web-app"],
        bio=BioInfo(
            text="你好。", text_en="Hi.", input_hash="sha256:x", en_dropped_hash="sha256:y"
        ),
        readme_last="# 上次写入的 README\n",
        repo_descriptions={
            "alice/cool-tool": {
                "baseline": "旧描述",
                "previous": "旧描述",
                "written": "新描述",
                "pushed_at": "2026-10-20T00:00:00+00:00",
                "generated_at": "2026-10-25T00:00:00+00:00",
                "manual": False,
                "attempts": 1,
            }
        },
    )
    p = tmp_path / "snapshot.json"
    save_snapshot(p, snap)
    got = load_snapshot(p)
    assert got is not None
    assert got.schema == 5
    assert got.pins == ["alice/cool-tool", "alice/web-app"]
    assert got.bio.text_en == "Hi."
    assert got.bio.en_dropped_hash == "sha256:y"
    assert got.readme_last == "# 上次写入的 README\n"
    assert got.repo_descriptions["alice/cool-tool"]["written"] == "新描述"
    assert got.repo_descriptions["alice/cool-tool"]["attempts"] == 1


def test_schema4_upgrades_to_5_with_empty_repo_descriptions(tmp_path):
    """旧快照（schema 4、无描述写回字段）读入自动升级：schema=5、空缺省。"""
    old = {
        "schema": 4,
        "username": "alice",
        "stats_fingerprint": "sha256:abc",
        "repos": [],
        "bio": {"text": "你好。", "input_hash": "h", "generated_at": "", "model": "", "prompt_version": 1},
        "repo_readmes": {},
        "generated_at": "",
    }
    p = tmp_path / "snapshot.json"
    p.write_text(json.dumps(old, ensure_ascii=False), encoding="utf-8")
    got = load_snapshot(p)
    assert got is not None
    assert got.schema == 5
    assert got.repo_descriptions == {}
    assert got.pins == []
    assert got.bio.text_en == ""
    assert got.readme_last == ""


def test_malformed_repo_descriptions_entries_are_normalized(tmp_path):
    """坏条目丢弃、字符串字段补空、manual 强制布尔——残缺快照不得让进程崩溃。"""
    p = tmp_path / "snapshot.json"
    p.write_text(
        json.dumps(
            {
                "schema": 5,
                "repo_descriptions": {
                    "alice/x": {"baseline": "b", "manual": 1},
                    "alice/y": "坏条目",
                },
            }
        ),
        encoding="utf-8",
    )
    got = load_snapshot(p)
    assert got is not None
    assert set(got.repo_descriptions) == {"alice/x"}
    entry = got.repo_descriptions["alice/x"]
    assert entry == {
        "baseline": "b",
        "previous": "",
        "written": "",
        "pushed_at": "",
        "generated_at": "",
        "manual": True,
        "attempts": 0,
    }


def test_repo_descriptions_attempts_roundtrip_and_bad_value_defaults(tmp_path):
    """attempts 往返保留；坏值（字符串等）按 0 处理，不崩。"""
    p = tmp_path / "snapshot.json"
    p.write_text(
        json.dumps(
            {
                "schema": 5,
                "repo_descriptions": {
                    "alice/b": {"baseline": "b", "attempts": 2},
                    "alice/c": {"baseline": "c", "attempts": "坏值"},
                },
            }
        ),
        encoding="utf-8",
    )
    got = load_snapshot(p)
    assert got is not None
    assert got.repo_descriptions["alice/b"]["attempts"] == 2
    assert got.repo_descriptions["alice/c"]["attempts"] == 0
