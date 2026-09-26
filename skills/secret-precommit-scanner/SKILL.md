---
name: secret-precommit-scanner
description: Stop secrets from ever being committed. Checks that sensitive files (.env, serviceAccountKey.json, credentials.json, *.pem, private keys) are in .gitignore, scans staged changes and the working tree for API keys, tokens, SMTP credentials, app-specific passwords, service-account keys and high-entropy strings, and installs a git pre-commit hook that blocks the commit if any are found. Use this whenever the user mentions secrets, API keys, .env, .gitignore, credentials, "is it safe to commit/push", pre-commit hooks, or right after cloning or setting up a repo.
trigger: auto
after: repo-clone
depends_on: []
parallel_group: security
produces: [onboarding/secrets_precommit.json]
local_tools: [scripts/precommit_scan.py --working-tree --json]
---

# Secret Pre-Commit Scanner

A leaked key is expensive and hard to undo once it's in git history. This skill catches secrets **before** they get there: it audits `.gitignore`, scans what's about to be committed, and installs a hook so every future commit is checked automatically. Accuracy matters both ways: a missed key is dangerous, and too many false alarms make people disable the hook.

## Tools in this skill
- `scripts/precommit_scan.py`: the scanner (standard-library Python, no install needed)
  - `--staged`: scan staged changes; exit code 1 means block the commit
  - `--working-tree`: scan every tracked and untracked (non-ignored) file
  - `--install-hook`: install `.git/hooks/pre-commit`, which runs `--staged` on every commit
  - `--json`: machine-readable output
- `scripts/secret_patterns.py`: the detection rules (known key formats, secret assignments, connection strings, private keys, service-account JSON, SMTP and app passwords, high-entropy strings). Extend it if the project uses other providers.

If `gitleaks` is installed, you may run `gitleaks protect --staged` as a second opinion, but the bundled script is the default because it works everywhere.

## Workflow

1. **Audit `.gitignore`.** Run `python <skill>/scripts/precommit_scan.py --working-tree`. Its `[GITIGNORE]` lines list sensitive files that are either not ignored or already tracked.
   - Not ignored: propose the exact `.gitignore` lines to add (e.g. `.env`, `.env.*`, `!.env.example`, `*.pem`, `serviceAccountKey.json`, `credentials.json`) and add them once the user agrees.
   - Already tracked: ignoring isn't enough. Propose `git rm --cached <file>` (this keeps the local file) and warn that the file is still in history, so the **git-history-secret-audit** skill should run next.

2. **Scan the working tree.** Review each `[SECRET]` finding in context before reporting it. Common false positives are placeholders, test fixtures, public keys, and values read from `process.env` / `import.meta.env` / `os.environ`. For each real secret, recommend moving it to `.env` (ignored), referencing it via environment variables, and adding the variable name to `.env.example`.

3. **Install the hook.** With the user's OK, run `--install-hook`. Then demonstrate it: stage a harmless fake key such as `sk-TESTFAKEKEY1234567890abcd`, show that the commit is blocked, and unstage it. If the repo already has a pre-commit hook, don't overwrite it. Chain the scanner into the existing hook instead, and say so.

4. **False positives.** A line can be allowed by adding the comment `secret-scan: allow` to it. Only suggest this after you've confirmed the value isn't a real secret.

## Report
Save `onboarding/secrets_precommit.json` (the `--json` output plus the actions taken) and give a short chat summary:
- **.gitignore status:** fixed, OK, or needs action
- **Secrets in the working tree:** how many, with the file and type of each (masked)
- **Hook:** installed or not
- **Next step:** run git-history-secret-audit if anything sensitive was ever tracked

## Rules
- Never print, log or copy a secret's value. Always mask it (first 5 characters + `...[masked]`).
- Never commit, push, or delete a user's files without asking.
- Blocking commits is the point, so don't weaken a pattern just to make a finding go away. Fix the code instead.
