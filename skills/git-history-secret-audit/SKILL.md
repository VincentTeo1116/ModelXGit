---
name: git-history-secret-audit
description: Audit a repository's entire git history (all branches and tags) for secrets and sensitive files that were ever committed, even if deleted later — produce a report of affected files, commit IDs and credential types — then interactively offer to purge them with git-filter-repo and give post-incident guidance to rotate every exposed credential. Use this whenever the user asks about leaked keys, secrets in git history, "was anything exposed", cleaning or rewriting history, removing a .env from git, or after cloning a repo for onboarding.
trigger: auto
after: repo-clone
depends_on: []
parallel_group: security
produces: [onboarding/secrets_history.json, onboarding/secrets_history_report.md]
local_tools: [scripts/history_scan.py]
---

# Git History Secret Audit & Remediation

Deleting a secret in a new commit does **not** remove it: anyone who clones the repo can still read it in the old commits. This skill finds every past exposure, explains the risk clearly, and, only if the user explicitly agrees, rewrites history to purge it. Rewriting history is destructive and affects everyone who has cloned the repo, so it must be careful, backed up and consented to. And no matter what, **exposed credentials must be rotated**: purging history only stops future leaks, not the ones that already happened.

## Tools in this skill
- `scripts/history_scan.py`: scans `git log --all -p` (every branch and tag) with the same rules as the pre-commit scanner. Values are always masked. Run it as `python <skill>/scripts/history_scan.py --out onboarding/secrets_history.json`.
- `scripts/secret_patterns.py`: the shared detection rules.
- If `gitleaks` is available, also run `gitleaks detect --source . --log-opts="--all" --report-path onboarding/gitleaks.json --redact` and merge the results.

## When the onboarding backend runs this skill

If your prompt points to `onboarding/.context/`, the backend has already run `history_scan.py` over the full clone: its result is in `context.local_tool_results`. Do Phase 1 steps 3–4 from that result and write `onboarding/secrets_history.json` (the scan result plus your review) and the report. **Stop there:** don't run Phase 2. Put the rewrite offer and the rotation list in the report as actions for the developer, and always include the post-incident guidance.

## Phase 1: Audit (read-only)
1. Make sure all refs are present: `git fetch --all --tags` (if there's a remote).
2. Run the scan(s).
3. Review the findings in context and drop obvious false positives (placeholders, test fixtures, public or anon keys that are designed to be public, but say so explicitly, e.g. "Supabase anon key: public by design, but still rotate it if RLS isn't enforced").
4. Write `onboarding/secrets_history_report.md` with:
   - a summary: commits scanned, number of findings, affected files
   - a table: **commit ID (short) · date · file · credential type · still in current files?**
   - a severity per finding (critical: private keys, cloud or service-account keys, passwords; high: API tokens; low: public or anon keys)
   - which credentials must be rotated
   Keep values masked everywhere.
5. Write `onboarding/secrets_history.json` in exactly this shape (the dashboard reads it; values are only an example):
   ```json
   {
     "summary": {"commits_scanned": 82, "findings": 3, "confirmed": 1, "false_positives": 2, "rotate": ["Supabase anon key"]},
     "findings": [
       {"commit": "ddbb86b4c7", "date": "2025-12-31", "file": "index.html", "type": "JWT / Supabase key",
        "value": "eyJhb...[masked]", "still_in_current_files": false, "severity": "low",
        "false_positive": false, "reason": "one line on why"}
     ]
   }
   ```

## Phase 2: Interactive remediation (only with explicit consent)
Present the report, then ask plainly:

> "Do you want to rewrite git history to permanently remove these files/secrets? This changes commit IDs on every branch, requires a force-push, and everyone else must re-clone. Yes / No / Only these files: …"

Do nothing destructive without a clear yes. If they agree:

1. **Rotate first.** Remind them to revoke and rotate the exposed credentials before anything else, since rewriting takes time and the secrets are already exposed.
2. **Back up.** Run `git clone --mirror <repo> ../<name>-backup.git` (or copy the folder), and confirm it exists.
3. **Check the tool.** Check for `git filter-repo --version`; if it's missing, suggest `pip install git-filter-repo`. Don't use the deprecated `git filter-branch`.
4. **Purge.** Build the command from the findings and show it before running:
   - Whole files: `git filter-repo --invert-paths --path .env --path serviceAccountKey.json`
   - Secrets inside files that must stay: create `replacements.txt` with lines like `<exact-secret>==>REMOVED_SECRET`, run `git filter-repo --replace-text replacements.txt`, then **delete `replacements.txt` immediately** (it contains the raw secrets), and never commit it or print it.
   - filter-repo expects a fresh clone. If it refuses, explain why and do the purge in a fresh clone, rather than reaching for `--force` by default.
5. **Verify.** Re-run `history_scan.py`. The purged findings must be gone.
6. **Publish (the user runs it or confirms it).** filter-repo removes the `origin` remote, so re-add it, then `git push --force --all` and `git push --force --tags`. Tell collaborators to re-clone (not pull). Mention that open pull requests, forks and host caches may still hold the old commits: on GitHub, contact support to purge cached views or PR refs if needed.

## Post-incident guidance (always include it, even if they decline the rewrite)
- **Rotate or revoke every exposed credential now.** List them with where to do it (provider dashboard or console).
- Check provider logs for any use of the exposed keys since the commit date.
- Move secrets to `.env` or a secrets manager, add `.env.example`, and fix `.gitignore`.
- Install the pre-commit hook from the **secret-precommit-scanner** skill so it doesn't happen again.
- Turn on the host's secret scanning / push protection (e.g. GitHub → Settings → Code security).

## Rules
- Never display, log or commit a secret value. Mask everything, including values you believe are fake, test or placeholder keys: quote them masked too (first 5 characters + `...[masked]`).
- Never rewrite history, force-push, or delete branches without explicit confirmation of that specific action.
- Always make a backup before rewriting.
- Be honest: rotating credentials matters more than purging history.
