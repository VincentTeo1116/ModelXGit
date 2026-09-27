"""Skill definitions and the local secret scanners."""
import json
import re
import shutil
import subprocess
import sys

import pytest
import yaml

from conftest import HISTORY, ROOT, SCANNER, git
import orchestrator as o

sys.path.insert(0, str(SCANNER))
from secret_patterns import scan_line  # noqa: E402


def test_registry_loads_all_skills_without_warnings():
    reg = o.SkillRegistry(ROOT / "skills")
    assert len(reg.skills) == 8 and not reg.warnings
    names = lambda t: sorted(s.name for s in reg.by_trigger(o.SkillTrigger(t)))
    assert names("entry") == ["repo-clone"]
    assert names("manual") == ["codebase-qa", "readme-generator"]
    assert len(names("auto")) == 5
    assert reg.get("architecture-diagram").depends_on == ["tech-stack-detection"]


def test_skills_follow_bob_loader_rules():
    for md in (ROOT / "skills").glob("*/SKILL.md"):
        meta = yaml.safe_load(re.match(r"^---\s*\n(.*?)\n---", md.read_text(encoding="utf-8"), re.S).group(1))
        assert meta["name"] == md.parent.name
        assert re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", meta["name"]) and len(meta["name"]) <= 64
        assert meta["description"].strip()


def test_both_scanner_rule_copies_identical():
    other = ROOT / "skills" / "git-history-secret-audit" / "scripts" / "secret_patterns.py"
    assert (SCANNER / "secret_patterns.py").read_bytes() == other.read_bytes()


@pytest.mark.parametrize("line, flagged", [
    ("const u = '%VITE_SUPABASE_URL%';", False),
    ('api_key = "your_bob_api_key_here"', False),
    ('KEY = "<your-anon-key>"', False),
    ('token = "${GITHUB_TOKEN}"', False),
    ('password = "xxxxxxxxxx"', False),
    ('api_key = "sk-XXXXXXXXXXXXXXXXXXXXXXXX"', False),
    ('token = "sk-TESTFAKEKEY1234567890abcd"', True),  # secret-scan: allow (fake test key)
    ('api_key = "q8XxXz71LmN0pQ4rS9tU"', True),  # secret-scan: allow (fake test key)
])
def test_placeholder_rules(line, flagged):
    assert bool(scan_line(line)) is flagged


def test_history_scan_reports_two_different_keys_in_one_file(make_repo):
    repo = make_repo({"c.js": "a='eyJhbGciOiJIUzI1NiJ9.eyJyb2xlIjoiYW5vbiJ9.FAKESIGNATUREaaaaaaaaaaaa'\n"})  # secret-scan: allow (fake test value)
    with open(repo / "c.js", "a") as f:
        f.write("b='eyJhbGciOiJIUzI1NiJ9.eyJyb2xlIjoic2VydmljZV9yb2xlIn0.FAKESIGNATUREbbbbbbbbbbbb'\n")  # secret-scan: allow (fake test value)
    git(repo, "commit", "-qam", "second key")
    out = json.loads(subprocess.run([sys.executable, str(HISTORY)], cwd=repo, capture_output=True, text=True).stdout)
    assert sum(f["type"].startswith("JWT") for f in out["findings"]) == 2
    assert all("[masked]" in f["value"] for f in out["findings"])


def test_history_scan_skips_lockfiles(make_repo):
    repo = make_repo({"package-lock.json": '{"integrity": "sha512-' + "A" * 40 + 'b1c2d3e4f5g6h7i8j9k0lmnopqrstuvwxyz0123456789"}\n'})  # secret-scan: allow (fake test value)
    out = json.loads(subprocess.run([sys.executable, str(HISTORY)], cwd=repo, capture_output=True, text=True).stdout)
    assert out["findings"] == []


def test_precommit_hook_blocks_secrets_and_allows_clean_commits(make_repo, tmp_path):
    scripts = tmp_path / "scanner"
    shutil.copytree(SCANNER, scripts)
    repo = make_repo({"a.py": "x = 1\n"})
    subprocess.run([sys.executable, str(scripts / "precommit_scan.py"), "--install-hook"], cwd=repo, check=True, capture_output=True)
    (repo / "b.py").write_text("y = 2\n"); git(repo, "add", "b.py")
    assert git(repo, "commit", "-qm", "clean").returncode == 0
    (repo / "leak.py").write_text('token = "sk-TESTFAKEKEY1234567890abcd"\n'); git(repo, "add", "leak.py")  # secret-scan: allow
    assert git(repo, "commit", "-qm", "leak").returncode != 0
    git(repo, "rm", "-q", "--cached", "leak.py")
    scripts.rename(tmp_path / "moved")
    (repo / "c.py").write_text("z = 3\n"); git(repo, "add", "c.py")
    moved = git(repo, "commit", "-qm", "moved")
    assert moved.returncode != 0 and "scanner not found" in moved.stderr
