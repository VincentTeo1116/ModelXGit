---
name: setup-dependencies
description: Detect a project's languages and package managers, install its dependencies, prepare environment files, open the official pages to get any API keys the project needs, and verify it actually runs — then report the exact working commands. Use this whenever the user wants to "set up", "install", "get the project running", "run it locally", "fix setup", "what API keys do I need", or right after a repo has been cloned for onboarding, even if they only say "make it work".
trigger: auto
after: repo-clone
depends_on: []
parallel_group: setup
produces: [onboarding/setup.json, onboarding/setup_report.md]
---

# Setup Dependencies

A new developer's worst first day is a README that doesn't work. Your job is to take a freshly cloned repo to a verified runnable state, and to write down exactly what worked so nobody has to rediscover it. Be honest: a clear "this step fails, here's why" is far more useful than a false "setup complete".

## Approach

1. **Read before you run.** Start from `onboarding/clone_report.json` if it exists, then the README and any setup docs. Scan for manifests and version pins:
   - JavaScript/TypeScript: `package.json` + lockfile (`package-lock.json` → npm, `yarn.lock` → yarn, `pnpm-lock.yaml` → pnpm, `bun.lockb` → bun), `.nvmrc`, `engines`
   - Python: `requirements*.txt`, `pyproject.toml` (poetry/uv/pdm/hatch), `Pipfile`, `setup.py`, `.python-version`
   - Java/Kotlin: `pom.xml`, `build.gradle(.kts)` and the wrapper scripts
   - Others: `go.mod`, `Cargo.toml`, `Gemfile`, `composer.json`, `*.csproj`/`*.sln`, `mix.exs`
   - Containers: `Dockerfile`, `docker-compose.yml`
   - Monorepos: multiple manifests or workspaces. Set up each part and note which is the main app.
   Also notice when a project has no manifest at all (e.g. React loaded from a CDN in an HTML file) and say so.

2. **Check the toolchain.** Verify the required runtimes and tools are installed and at compatible versions (`node -v`, `python --version`, `java -version`, `docker --version`...). If something is missing or the wrong version, stop and tell the user exactly what to install. Don't install system-wide tools without asking.

3. **Environment files.** If `.env.example` (or similar) exists and `.env` doesn't, create `.env` from it with the placeholder values unchanged, and list which variables the developer must fill in and where they come from (if the code or docs say). Never invent credentials, never print real secret values, never commit `.env`. If the app needs external services (database, Supabase, Firebase, APIs), say which ones and whether the app has any offline or mock fallback.

4. **API keys helper (open the right pages for the developer).** If the project uses external APIs or services, help the developer get each key quickly, without ever handling the secret yourself:
   - **Find what's needed.** Collect every required key from `.env.example`, env variable reads in the code (`process.env.X`, `import.meta.env.X`, `%VITE_X%`, `os.environ["X"]`), SDK clients (Supabase, Firebase, Stripe, OpenAI, watsonx, Google Maps, SMTP...), and the README. For each one, work out the provider, what the key is for, whether it's required or optional, and whether a free tier or test mode exists.
   - **Find the official page** where that key is created: the provider's console or API-keys page, or a link given in the project's README or docs. Only use official provider domains. If you're not sure of the exact page, use the provider's official docs page on API keys rather than guessing a deep link.
   - **Ask, then open the tabs.** Show the list (variable → provider → page) and ask: "Open these pages in your browser?" If yes, open each one in a new tab with the OS command (Windows: `start "" "<url>"`, macOS: `open "<url>"`, Linux: `xdg-open "<url>"`).
   - **Give short instructions for each tab:** sign in or sign up, where to click, which key type to create (least privilege: test/sandbox keys, read-only or restricted scopes, no admin or service-role keys unless truly required), and exactly which variable in `.env` to paste it into.
   - **The developer creates and pastes the keys, not you.** Creating keys needs their own account login, terms acceptance and sometimes billing, and a secret should never pass through the chat. Never ask them to paste a key into the chat. If they do anyway, tell them not to, don't repeat it, and suggest rotating it.
   - **Verify without revealing.** After they've pasted the keys, check that each variable in `.env` is present, non-empty and no longer a placeholder (report only "filled" or "missing", never the value). Where possible, do a harmless connectivity check (e.g. start the app and confirm it connects) and report the result.
   - Make sure `.env` is in `.gitignore` before any keys are added.

5. **Install.** Use the project's own package manager and lockfile (`npm ci` when a lockfile exists, otherwise `npm install`; Python in a virtual environment, never the global interpreter). Install scripts can execute code, so show the command and get approval before running it. Capture errors verbatim.

6. **Verify it runs.** Try, in order and only if they exist: build → tests → start the dev server or app for a short smoke check (confirm it starts and responds, then stop it). Use the project's scripts (`package.json` scripts, Makefile, etc.) rather than inventing commands. If there are no tests, say "no tests found", not "tests passed".

7. **Fix only what's clearly setup-related.** For example a missing folder, a wrong script path, or an outdated command in the docs. Propose the fix and explain it; don't change application logic.

Work in the user's shell. On Windows, prefer PowerShell-compatible commands and mention path differences where they matter.

## Output

Save to `onboarding/` (create it if needed):

- `onboarding/setup.json`:
  ```json
  {
    "toolchain": [{"tool": "node", "required": ">=18", "found": "20.11.0", "ok": true}],
    "env": {"file_created": ".env", "variables_to_fill": ["VITE_SUPABASE_URL"], "external_services": ["Supabase"]},
    "api_keys": [
      {"variable": "VITE_SUPABASE_ANON_KEY", "provider": "Supabase", "purpose": "database access from the browser", "required": true, "key_page": "https://supabase.com/dashboard", "opened_in_browser": true, "status": "filled | missing"}
    ],
    "steps": [
      {"name": "install", "command": "npm ci", "cwd": "educonnect", "status": "success | failed | skipped", "notes": "..."}
    ],
    "run_command": "npm run dev",
    "tests": "passed | failed | none found",
    "issues": ["..."]
  }
  ```
- `onboarding/setup_report.md`: the exact copy-paste commands that worked, in order, plus the issues found and how to fix them.

End with a short chat summary: status (ready / partly ready / blocked), how to run it, and what the developer still has to do themselves.
