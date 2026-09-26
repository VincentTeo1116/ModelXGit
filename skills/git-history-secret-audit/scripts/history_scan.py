#!/usr/bin/env python3
"""Scan full git history (all branches and tags) for secrets and sensitive files.
Usage: python history_scan.py [--out onboarding/secrets_history.json]
Never prints secret values; findings are masked.
"""
import fnmatch, json, os, subprocess, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from secret_patterns import SENSITIVE_FILES, SAFE_ENV_SUFFIXES, is_lockfile, scan_line_full  # noqa: E402


def git(*args):
    return subprocess.run(["git", *args], capture_output=True, text=True, encoding="utf-8", errors="replace").stdout


def sensitive(path):
    n = os.path.basename(path)
    return not n.endswith(SAFE_ENV_SUFFIXES) and any(fnmatch.fnmatch(n, p) for p in SENSITIVE_FILES)


def main():
    out = sys.argv[sys.argv.index("--out") + 1] if "--out" in sys.argv else None
    current_files = set(git("ls-files").splitlines())
    log = git("log", "--all", "-p", "-U0", "--no-color", "--format=@@COMMIT %H %ad", "--date=short")
    findings, seen = [], set()
    commit = date = path = None
    for line in log.splitlines():
        if line.startswith("@@COMMIT "):
            _, commit, date = line.split(" ", 2)
        elif line.startswith("+++ b/"):
            path = line[6:]
            if sensitive(path) and (commit, path) not in seen:
                seen.add((commit, path))
                findings.append({"commit": commit[:10], "date": date, "file": path, "type": "Sensitive file committed",
                                 "value": "-", "file_still_exists": path in current_files})
        elif line.startswith("+") and not line.startswith("+++") and path and not is_lockfile(path):
            for kind, masked, fp in scan_line_full(line[1:]):
                # De-duplicate on the full value: masked values of different
                # keys often look identical (every JWT starts "eyJhb").
                key = (path, kind, fp)
                if key in seen:
                    continue
                seen.add(key)
                findings.append({"commit": commit[:10], "date": date, "file": path, "type": kind,
                                 "value": masked, "file_still_exists": path in current_files})
    report = {
        "refs_scanned": "all branches and tags (--all)",
        "commits_scanned": int(git("rev-list", "--all", "--count").strip() or 0),
        "findings": findings,
        "affected_files": sorted({f["file"] for f in findings}),
    }
    text = json.dumps(report, indent=2)
    if out:
        os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
        open(out, "w", encoding="utf-8").write(text)
    print(text)


if __name__ == "__main__":
    main()
