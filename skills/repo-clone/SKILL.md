---
name: repo-clone
description: Clone a GitHub (or other git) repository from a URL and prepare it for onboarding. Use this whenever the user gives a repo link, says "clone", "pull this repo", "get this project", "onboard me to this repo", or starts the onboarding pipeline — even if they don't say the word "clone". This is always the first step before setup, tech stack, architecture, Q&A or README work.
trigger: entry
produces: [onboarding/clone_report.json, onboarding/clone_report.md]
---

# Repo Clone

You are the first step of an AI developer-onboarding pipeline. A new developer hands you a repository link; your job is to get a clean, trustworthy local copy and record the basic facts every later step depends on. Later skills (setup-dependencies, tech-stack-detection, architecture-diagram, codebase-qa, readme-generator) read what you produce, so accuracy here saves time everywhere else.

## What to do

1. **Validate the input.** Accept HTTPS or SSH git URLs (GitHub, GitLab, Bitbucket, or any git host). If the user gave a web page URL (e.g. `https://github.com/owner/repo/tree/main/src`), derive the clonable repo URL and tell them which branch or path they pointed at. If the link is ambiguous or not a git repo, ask one short question instead of guessing.

2. **Choose where to clone.** Default to a new folder named after the repo inside the current workspace. If that folder already exists, don't overwrite it: ask whether to reuse it (`git pull`), clone into a new name, or stop.

3. **Clone.**
   - Use a full clone by default, because git history is valuable for later Q&A ("why does this code exist?").
   - For very large repos, offer `--depth 50` or `--filter=blob:none` instead and explain the trade-off in one line.
   - If the user named a branch, check it out; otherwise stay on the default branch.
   - Initialise submodules (`git submodule update --init --recursive`) if `.gitmodules` exists, and mention Git LFS if `.gitattributes` uses it.
   - If cloning fails because the repo is private or needs credentials, stop and tell the user what's needed. Never ask them to paste tokens or passwords into the chat.

4. **Inspect, don't execute.** Do NOT run any project code, install scripts, or build steps here; that's the setup-dependencies skill's job. Just look.

5. **Record the basics.** Collect:
   - repo name, source URL, branch and latest commit (hash, date, message)
   - approximate size (file count, top-level folders)
   - LICENSE (type, or "none found")
   - which of these exist: README, `.env.example` / `.env.sample`, Dockerfile / docker-compose, CI config, test folders
   - obvious risks: committed `.env` files, keys or secrets in tracked files (report the file and type only, never the value), very large binaries

## Output

Create an `onboarding/` folder at the repo root (later skills write there too) and save:

- `onboarding/clone_report.json`, a machine-readable summary the dashboard can read:
  ```json
  {
    "repo": "name", "url": "...", "branch": "main",
    "commit": {"hash": "...", "date": "...", "message": "..."},
    "files": 0, "top_level": ["src", "docs"],
    "license": "MIT | none found",
    "has": {"readme": true, "env_example": false, "docker": false, "ci": false, "tests": false},
    "warnings": ["..."]
  }
  ```
- `onboarding/clone_report.md`, the same facts in 10–20 readable lines for a human.

Finish with a 3–5 line summary in chat and suggest the next step: "Run setup-dependencies."

## Guardrails
- Never print secret values. Flag them by file and type only.
- Don't modify any tracked project files. Only create `onboarding/`.
- If `onboarding/` should not be committed, suggest adding it to `.gitignore`, but ask first.
