"""projects.py 项目介绍生成测试（假 client，无网络）。"""

from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from profile_agent.config import AIConfig, ProjectsConfig
from profile_agent.models import Repo
from profile_agent.projects import (
    build_intro_prompt,
    generate_project_intro,
    intro_filename,
    intro_input_hash,
    update_project_intros,
)

REPO = Repo(
    name="cool-tool",
    full_name="alice/cool-tool",
    description="一个很酷的工具",
    language="Python",
    stars=120,
    forks=12,
    pushed_at=datetime(2026, 10, 1, tzinfo=timezone.utc),
    html_url="https://github.com/alice/cool-tool",
)


class FakeClient:
    def __init__(self, blocks):
        self.calls = []
        self._blocks = blocks
        self.messages = self

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=t) for t in self._blocks])


def test_intro_filename_safe():
    assert intro_filename(REPO) == "cool-tool.md"
    assert intro_filename(Repo(name="a/b", full_name="x/a/b")) == "a-b.md"


def test_intro_input_hash_changes_with_repo_data_or_version():
    cfg = AIConfig(prompt_version=1)
    h1 = intro_input_hash(REPO, cfg)
    assert h1.startswith("sha256:")
    assert intro_input_hash(REPO, cfg) == h1
    bumped = Repo(**{**REPO.__dict__, "stars": 121})
    assert intro_input_hash(bumped, cfg) != h1
    assert intro_input_hash(REPO, AIConfig(prompt_version=2)) != h1


def test_generate_project_intro_content():
    client = FakeClient(["本工具解决了X问题。", "\n\n亮点：\n- 支持 A\n- 性能提升 3 倍"])
    text = generate_project_intro(REPO, AIConfig(model="m"), client=client)
    assert "本工具解决了X问题" in text
    assert "亮点" in text
    assert client.calls[0]["model"] == "m"


def test_force_regenerates_even_when_hash_unchanged(tmp_path: Path, monkeypatch):
    """force=True（点名重跑/--force）：hash 没变也强制重新生成。"""
    import profile_agent.projects as projects_mod

    calls = []

    def fake_gen(repo, cfg, **kwargs):
        calls.append(repo.name)
        return f"第 {len(calls)} 版介绍"

    monkeypatch.setattr(projects_mod, "generate_project_intro", fake_gen)
    proj = ProjectsConfig(dir=str(tmp_path / "docs" / "projects"))
    ai = AIConfig()
    s1 = update_project_intros([REPO], proj, ai, {})
    assert len(calls) == 1
    s2 = update_project_intros([REPO], proj, ai, s1)
    assert len(calls) == 1  # hash 未变 → 复用
    assert "regenerated" not in s2[REPO.full_name]  # 复用状态不带重跑标记
    s3 = update_project_intros([REPO], proj, ai, s2, force=True)
    assert len(calls) == 2  # 强制重跑
    assert s3[REPO.full_name].get("regenerated") is True
    body = (tmp_path / "docs" / "projects" / "cool-tool.md").read_text(encoding="utf-8")
    assert "第 2 版介绍" in body


def test_update_intros_skips_unchanged_and_missing_file(tmp_path: Path):
    proj = ProjectsConfig(dir=str(tmp_path / "docs/projects"))
    ai = AIConfig()
    client = FakeClient(["生成的介绍内容。"])
    prev_states = {
        "alice/cool-tool": {
            "intro_hash": intro_input_hash(REPO, ai),
            "intro_generated_at": "2026-10-01T00:00:00+00:00",
            "ai_generated": True,
        }
    }
    # 文件已存在且 hash 匹配 → 跳过，不调 AI
    out_dir = tmp_path / "docs" / "projects"
    out_dir.mkdir(parents=True)
    (out_dir / "cool-tool.md").write_text("已有内容", encoding="utf-8")
    infos = update_project_intros(
        [REPO], proj, ai, prev_states, client=client, allow_ai=True
    )
    assert client.calls == []
    assert infos["alice/cool-tool"]["intro_hash"] == prev_states["alice/cool-tool"]["intro_hash"]
    assert (out_dir / "cool-tool.md").read_text(encoding="utf-8") == "已有内容"


def test_update_intros_generates_when_file_missing(tmp_path: Path):
    proj = ProjectsConfig(dir=str(tmp_path / "docs/projects"))
    ai = AIConfig()
    client = FakeClient(["新生成的项目介绍。"])
    prev_hash = intro_input_hash(REPO, ai)
    infos = update_project_intros(
        [REPO],
        proj,
        ai,
        {
            "alice/cool-tool": {
                "intro_hash": prev_hash,
                "intro_generated_at": "2026-10-01T00:00:00+00:00",
            }
        },
        client=client,
        allow_ai=True,
    )
    assert len(client.calls) == 1
    out = (tmp_path / "docs" / "projects" / "cool-tool.md").read_text(encoding="utf-8")
    assert "新生成的项目介绍" in out
    assert "github.com/alice/cool-tool" in out  # 文件头带仓库链接
    assert infos["alice/cool-tool"]["intro_hash"] == prev_hash


def test_update_intros_no_ai_writes_data_stub(tmp_path: Path):
    """无 AI key 时：不调模型，但仍产出数据版介绍文件（结构完整，事实数据）。"""
    proj = ProjectsConfig(dir=str(tmp_path / "docs/projects"))
    infos = update_project_intros([REPO], proj, AIConfig(), {}, client=None, allow_ai=False)
    out = (tmp_path / "docs" / "projects" / "cool-tool.md").read_text(encoding="utf-8")
    assert "一个很酷的工具" in out  # 描述来自 API 数据
    assert "120" in out
    assert infos["alice/cool-tool"]["intro_hash"]
    assert infos["alice/cool-tool"]["ai_generated"] is False


def test_update_intros_ai_error_keeps_data_stub(tmp_path: Path):
    class Boom:
        def __init__(self):
            self.messages = self

        def create(self, **kwargs):
            raise RuntimeError("boom")

    proj = ProjectsConfig(dir=str(tmp_path / "docs/projects"))
    infos = update_project_intros([REPO], proj, AIConfig(), {}, client=Boom(), allow_ai=True)
    assert infos["alice/cool-tool"]["ai_generated"] is False
    assert (tmp_path / "docs" / "projects" / "cool-tool.md").exists()


# ---- 信息熵优化：README 作为事实来源喂给模型 ----


def test_build_intro_prompt_includes_readme_context():
    prompt = build_intro_prompt(REPO, readme="# cool-tool\n实现了 X 编译器，支持插件化架构")
    assert "README" in prompt
    assert "X 编译器" in prompt and "插件化架构" in prompt
    assert "一个很酷的工具" in prompt  # 已知事实仍在


def test_build_intro_prompt_without_readme_says_so():
    prompt = build_intro_prompt(REPO)
    assert "没有可用的 README" in prompt
    assert "一个很酷的工具" in prompt


def test_build_intro_prompt_truncates_long_readme():
    prompt = build_intro_prompt(REPO, readme="x" * 20000)
    assert "已截断" in prompt
    assert len(prompt) < 10000  # 不会把超长 README 原样塞进 prompt


def test_update_intros_feeds_readme_to_ai_when_generating(tmp_path: Path):
    proj = ProjectsConfig(dir=str(tmp_path / "docs/projects"))
    ai = AIConfig(model="m")
    client = FakeClient(["有内容的项目介绍"])
    fetched: list[str] = []

    def fetch(full_name: str) -> str:
        fetched.append(full_name)
        return "# 工具说明\n支持插件化架构与热加载"

    infos = update_project_intros(
        [REPO], proj, ai, {}, client=client, allow_ai=True, fetch_readme=fetch
    )
    assert fetched == ["alice/cool-tool"]
    prompt = client.calls[0]["messages"][0]["content"]
    assert "支持插件化架构与热加载" in prompt
    assert infos["alice/cool-tool"]["ai_generated"] is True


def test_update_intros_fetch_failure_still_generates(tmp_path: Path):
    proj = ProjectsConfig(dir=str(tmp_path / "docs/projects"))
    client = FakeClient(["降级后的项目介绍。"])

    def boom(full_name: str) -> str:
        raise RuntimeError("网络错误")

    infos = update_project_intros(
        [REPO], proj, AIConfig(), {}, client=client, allow_ai=True, fetch_readme=boom
    )
    prompt = client.calls[0]["messages"][0]["content"]
    assert "没有可用的 README" in prompt  # 读取失败 → 退回只用事实
    assert infos["alice/cool-tool"]["ai_generated"] is True
    assert (tmp_path / "docs" / "projects" / "cool-tool.md").exists()


def test_update_intros_skips_fetch_when_unchanged(tmp_path: Path):
    proj = ProjectsConfig(dir=str(tmp_path / "docs/projects"))
    ai = AIConfig()
    client = FakeClient(["不该被调用"])
    out_dir = tmp_path / "docs" / "projects"
    out_dir.mkdir(parents=True)
    (out_dir / "cool-tool.md").write_text("已有内容", encoding="utf-8")
    fetched: list[str] = []

    update_project_intros(
        [REPO],
        proj,
        ai,
        {"alice/cool-tool": {"intro_hash": intro_input_hash(REPO, ai), "ai_generated": True}},
        client=client,
        allow_ai=True,
        fetch_readme=lambda name: fetched.append(name) or "README",
    )
    assert fetched == []  # 无变化 → 不拉 README、不调 AI


# ---- 数据版占位的生命周期：失败要可见、AI 可用后要自动升级 ----


def test_stub_upgraded_when_ai_becomes_available(tmp_path: Path):
    """先无 key 跑出数据版占位；AI 可用后的下一次运行应自动重写为 AI 版。"""
    proj = ProjectsConfig(dir=str(tmp_path / "docs/projects"))
    infos1 = update_project_intros([REPO], proj, AIConfig(), {}, client=None, allow_ai=False)
    assert infos1["alice/cool-tool"]["ai_generated"] is False
    out = tmp_path / "docs" / "projects" / "cool-tool.md"
    assert "数据版占位" in out.read_text(encoding="utf-8")

    client = FakeClient(["AI 写的介绍正文"])
    infos2 = update_project_intros(
        [REPO], proj, AIConfig(), infos1, client=client, allow_ai=True
    )
    assert infos2["alice/cool-tool"]["ai_generated"] is True
    assert len(client.calls) == 1
    assert "AI 写的介绍正文" in out.read_text(encoding="utf-8")


def test_stub_not_retried_when_ai_still_unavailable(tmp_path: Path):
    """AI 仍不可用时：占位版哈希命中 → 复用跳过，不重写文件。"""
    proj = ProjectsConfig(dir=str(tmp_path / "docs/projects"))
    infos1 = update_project_intros([REPO], proj, AIConfig(), {}, client=None, allow_ai=False)
    out = tmp_path / "docs" / "projects" / "cool-tool.md"
    before = out.read_text(encoding="utf-8")
    infos2 = update_project_intros([REPO], proj, AIConfig(), infos1, client=None, allow_ai=False)
    assert "regenerated" not in infos2["alice/cool-tool"]  # 走了复用跳过路径
    assert out.read_text(encoding="utf-8") == before


def test_ai_failure_prints_warning_and_still_writes_stub(tmp_path: Path, capsys):
    """AI 调用失败不能静默：stderr 必须留下失败原因（线上排查全靠它）。"""

    class Boom:
        def __init__(self):
            self.messages = self

        def create(self, **kwargs):
            raise RuntimeError("boom")

    proj = ProjectsConfig(dir=str(tmp_path / "docs/projects"))
    infos = update_project_intros([REPO], proj, AIConfig(), {}, client=Boom(), allow_ai=True)
    assert infos["alice/cool-tool"]["ai_generated"] is False
    err = capsys.readouterr().err
    assert "项目介绍" in err and "boom" in err
    assert (tmp_path / "docs" / "projects" / "cool-tool.md").exists()


def test_data_stub_names_configured_key_env(tmp_path: Path):
    """占位文案里的 secret 名要跟配置走（MiMo 用户配的是 AI_API_KEY）。"""
    proj = ProjectsConfig(dir=str(tmp_path / "docs/projects"))
    update_project_intros(
        [REPO], proj, AIConfig(key_env="AI_API_KEY"), {}, client=None, allow_ai=False
    )
    out = (tmp_path / "docs" / "projects" / "cool-tool.md").read_text(encoding="utf-8")
    assert "配置 AI_API_KEY 后" in out
    assert "ANTHROPIC_API_KEY" not in out
