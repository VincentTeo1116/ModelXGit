"""Clone safety, workspace containment, Bob IDE hand-off, output contract, masking, retry."""
import asyncio
import json
import subprocess

import pytest

from conftest import ROOT, git
import orchestrator as o

REG = o.SkillRegistry(ROOT / "skills")


@pytest.mark.parametrize("url", [
    "file:///C:/x", "C:\\x", "/etc/passwd", "--upload-pack=calc.exe", "https://user:tok@github.com/a/b.git",
    "ext::sh -c touch% /tmp/x", "http://github.com/a/b.git",
])
def test_unsafe_clone_urls_rejected(url):
    with pytest.raises(ValueError):
        o.normalize_repo_url(url, None)


def test_bad_branch_rejected_and_page_links_understood():
    with pytest.raises(ValueError):
        o.normalize_repo_url("https://github.com/a/b.git", "--foo")
    assert o.normalize_repo_url("https://github.com/a/b/tree/dev", None) == ("https://github.com/a/b.git", "dev")
    assert o.normalize_repo_url("git@github.com:a/b.git", None) == ("git@github.com:a/b.git", None)


def test_clone_command_is_hardened(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(o, "_git", lambda args, cwd=None, timeout=60: calls.append(args) or subprocess.CompletedProcess(args, 0, "", ""))
    o.clone_repo("https://github.com/a/b.git", None, tmp_path / "x")
    args = calls[0]
    assert "core.symlinks=false" in args and "protocol.allow=never" in args
    assert args[args.index("--") + 1] == "https://github.com/a/b.git" and "--depth" not in args


def test_containment_undoes_everything_outside_onboarding(make_repo):
    repo = make_repo({"main.py": "ok\n", "config.py": "ok\n", ".gitignore": ".env\n__pycache__/\n"})
    info = o.install_bob_ide_files(repo, REG)
    (repo / "main.py").write_text("hacked")
    (repo / "config.py").unlink()
    (repo / "new.txt").write_text("x")
    (repo / ".env").write_text("SECRET=1")
    (repo / "__pycache__").mkdir(); (repo / "__pycache__" / "a.pyc").write_text("x")
    (repo / ".bob/skills/repo-clone/evil.md").write_text("x")
    (repo / ".bob/custom_modes.yaml").write_text("customModes: []")
    (repo / "onboarding").mkdir(); (repo / "onboarding/ok.md").write_text("ok")
    (repo / "README.md").write_text("# ok")
    o.contain_workspace(repo, frozenset(info["installed"]))
    o.install_bob_ide_files(repo, REG)
    assert (repo / "main.py").read_text() == "ok\n" and (repo / "config.py").exists()
    assert not (repo / "new.txt").exists() and not (repo / ".env").exists() and not (repo / "__pycache__").exists()
    assert not (repo / ".bob/skills/repo-clone/evil.md").exists()
    assert "codebase-qa" in (repo / ".bob/custom_modes.yaml").read_text()
    assert (repo / "onboarding/ok.md").exists() and (repo / "README.md").exists() and (repo / ".git").is_dir()


def test_handoff_installs_skills_mode_and_bobignore_and_keeps_repo_files(make_repo):
    repo = make_repo({".bobignore": "dist/\n", ".bob/custom_modes.yaml": "customModes: [] # theirs\n"})
    info = o.install_bob_ide_files(repo, REG)
    assert len(info["skills"]) == 8 and ".bobignore" in info["installed"]
    assert "theirs" in (repo / ".bob/custom_modes.yaml").read_text()
    ignore = (repo / ".bobignore").read_text()
    assert ignore.startswith("dist/") and "*.pem" in ignore and "!.env.example" in ignore
    o.install_bob_ide_files(repo, REG)
    assert (repo / ".bobignore").read_text() == ignore  # stable, never appended twice


def test_repo_context_leaves_out_secrets_and_handoff(make_repo):
    repo = make_repo({"app.py": "x\n", ".env": "K=1\n", "package-lock.json": "{}\n", "certs/a.pem": "x\n"})
    info = o.install_bob_ide_files(repo, REG)
    ctx = o.build_repo_context(repo, frozenset(info["installed"]))
    sent = {f["path"] for f in ctx["files"]}
    assert "app.py" in sent and not sent & {".env", "package-lock.json", "certs/a.pem", ".bobignore"}
    assert not any(p.startswith(".bob/") for p in ctx["file_tree"])


def test_file_facts(make_repo):
    repo = make_repo({"LICENSE": "MIT", "README.md": "x", ".env.example": "A=", "tests/test_a.py": "", "src/a.py": ""})
    f = o.file_facts(repo)
    assert f["files"] == 5 and f["license"] == "LICENSE" and f["readme"] == "README.md"
    assert f["env_example"] == [".env.example"] and f["test_files"] == 1 and "src/" in f["top_level"]


def test_output_contract():
    assert o.validate_output("onboarding/setup.json", "{bad")[0].startswith("not valid JSON")
    assert o.validate_output("onboarding/tech_stack.json", json.dumps({"summary": 1, "categories": {}})) == \
        ["'summary' should be str, got int", "missing key 'absent'"]
    arch = {"summary": "s", "layers": [], "nodes": [{"id": "a", "label": "A", "layer": "api"}], "edges": [{"from": "a", "to": "b"}]}
    assert o.validate_output("onboarding/architecture.json", json.dumps(arch)) == ["edges point to unknown nodes: a->b"]
    leak = json.dumps({"summary": {}, "findings": [{"value": "sk-TESTFAKEKEY1234567890abcd"}]})  # secret-scan: allow
    assert any("unmasked secret" in p for p in o.validate_output("onboarding/secrets_history.json", leak))


def test_masking_keeps_placeholders_and_json_valid():
    text, n = o.mask_secrets(json.dumps({"v": "eyJhbGciOiJIUzI1NiJ9.eyJyb2xlIjoiYW5vbiJ9.abcdefghijklmnopqrstu",  # secret-scan: allow (fake test value)
                                         "p": "sk-XXXXXXXXXXXXXXXXXXXXXXXX"}))
    data = json.loads(text)
    assert n == 1 and data["v"] == "eyJhb...[masked]" and data["p"] == "sk-XXXXXXXXXXXXXXXXXXXXXXXX"


class _Bob:
    kind, reads_workspace = "http", False


@pytest.mark.parametrize("errors, calls, ok", [
    (["Bob Shell timed out after 900s"], 2, True),
    (["Bob API error 503", "Bob API unreachable"], 2, False),
    (["Bob Shell needs the IBM license accepted"], 1, False),
    (["Bob Shell failed (exit 1): 401 Unauthorized"], 1, False),
])
def test_retry_only_temporary_errors(errors, calls, ok):
    orch = o.Orchestrator(REG, _Bob())
    count = {"n": 0}

    async def call():
        count["n"] += 1
        if count["n"] <= len(errors):
            raise RuntimeError(errors[count["n"] - 1])
        return "done"

    async def run():
        try:
            return (await orch._call_bob(call))[0]
        except RuntimeError:
            return None
    assert (asyncio.run(run()) == "done") is ok and count["n"] == calls


def test_masked_values_are_not_flagged_again():
    text, n = o.mask_secrets('password = "Zx9fakeFAKEfake1234"')  # secret-scan: allow (fake test value)
    assert n == 1 and o.validate_output("onboarding/setup_report.md", text) == []  # no "unmasked" false alarm
    assert o.mask_secrets(text) == (text, 0)


def test_clone_retries_network_errors_only(monkeypatch, tmp_path):
    calls = []

    def flaky(url, branch, dest, depth=None):
        calls.append(1)
        if len(calls) < 2:
            raise RuntimeError("git clone failed: fatal: unable to access 'x': Could not resolve host: github.com")
    monkeypatch.setattr(o, "clone_repo", flaky)
    o.clone_with_retry("https://github.com/a/b.git", None, tmp_path / "a", pause=0)
    assert len(calls) == 2

    def missing(url, branch, dest, depth=None):
        calls.append(1)
        raise RuntimeError("git clone failed: repository not found, or it is private (not supported).")
    calls.clear()
    monkeypatch.setattr(o, "clone_repo", missing)
    with pytest.raises(RuntimeError):
        o.clone_with_retry("https://github.com/a/b.git", None, tmp_path / "b", pause=0)
    assert len(calls) == 1
