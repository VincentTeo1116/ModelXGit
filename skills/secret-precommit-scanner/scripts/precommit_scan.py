#!/usr/bin/env python3
"""Pre-commit secret scanner.
Usage:
  python precommit_scan.py --staged        # scan staged changes (used by the git hook); exit 1 blocks the commit
  python precommit_scan.py --working-tree  # scan all tracked + untracked, non-ignored files
  python precommit_scan.py --install-hook  # install .git/hooks/pre-commit
Never prints secret values; findings are masked.
"""
import fnmatch, json, os, subprocess, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from secret_patterns import SENSITIVE_FILES, SAFE_ENV_SUFFIXES, scan_line  # noqa: E402


def git(*args):
    return subprocess.run(["git", *args], capture_output=True, text=True, encoding="utf-8", errors="replace").stdout


def is_sensitive(path):
    name = os.path.basename(path)
    if name.endswith(SAFE_ENV_SUFFIXES):
        return False
    return any(fnmatch.fnmatch(name, pat) for pat in SENSITIVE_FILES)


def gitignore_audit():
    """Sensitive files that exist but are not ignored."""
    problems = []
    candidates = git("ls-files", "--cached", "--others", "--exclude-standard").splitlines()
    for p in candidates:
        if is_sensitive(p):
            tracked = bool(git("ls-files", "--error-unmatch", p).strip())
            problems.append({"file": p, "issue": "tracked by git" if tracked else "not covered by .gitignore"})
    return problems


def scan_staged():
    findings = []
    diff = git("diff", "--cached", "-U0", "--no-color")
    current = None
    for line in diff.splitlines():
        if line.startswith("+++ b/"):
            current = line[6:]
        elif line.startswith("+") and not line.startswith("+++") and current:
            for kind, masked in scan_line(line[1:]):
                findings.append({"file": current, "type": kind, "value": masked})
    staged = git("diff", "--cached", "--name-only").splitlines()
    for p in staged:
        if is_sensitive(p):
            findings.append({"file": p, "type": "Sensitive file staged", "value": "-"})
    return findings


def scan_working_tree():
    findings = []
    for p in git("ls-files", "--cached", "--others", "--exclude-standard").splitlines():
        if not os.path.isfile(p) or os.path.getsize(p) > 2_000_000:
            continue
        try:
            with open(p, encoding="utf-8", errors="ignore") as f:
                for n, line in enumerate(f, 1):
                    for kind, masked in scan_line(line):
                        findings.append({"file": p, "line": n, "type": kind, "value": masked})
        except OSError:
            pass
    return findings


def install_hook():
    hooks = git("rev-parse", "--git-path", "hooks").strip() or ".git/hooks"
    os.makedirs(hooks, exist_ok=True)
    script = os.path.abspath(__file__).replace("\\", "/")
    hook = os.path.join(hooks, "pre-commit")
    with open(hook, "w", newline="\n") as f:
        f.write("#!/bin/sh\n"
                f'PY=$(command -v python3 || command -v python)\n'
                f'"$PY" "{script}" --staged || exit 1\n')
    os.chmod(hook, 0o755)
    print(f"Installed pre-commit hook at {hook}")


def main():
    if "--install-hook" in sys.argv:
        install_hook(); return 0
    report = {"gitignore_problems": gitignore_audit()}
    report["findings"] = scan_working_tree() if "--working-tree" in sys.argv else scan_staged()
    blocked = bool(report["findings"] or report["gitignore_problems"])
    if "--json" in sys.argv:
        print(json.dumps(report, indent=2))
    else:
        for g in report["gitignore_problems"]:
            print(f"[GITIGNORE] {g['file']}: {g['issue']}")
        for fi in report["findings"]:
            loc = f"{fi['file']}:{fi.get('line', '')}".rstrip(":")
            print(f"[SECRET] {loc}  {fi['type']}  {fi['value']}")
        print("Commit BLOCKED: remove secrets / fix .gitignore (or mark a false positive with 'secret-scan: allow')."
              if blocked else "No secrets found.")
    return 1 if (blocked and "--staged" in sys.argv) else 0


if __name__ == "__main__":
    sys.exit(main())
