---
name: tech-stack-detection
description: Automatically detect and document a repository's full tech stack — languages, frontend, backend, database, auth, external services, build tools, testing, CI/CD and deployment — with versions and file evidence for every item. Use this whenever the user asks "what is this built with", "what's the stack", "which frameworks/database does it use", or during repo onboarding, even if they only ask about one layer.
trigger: auto
after: repo-clone
depends_on: []
parallel_group: analysis
produces: [onboarding/tech_stack.json, onboarding/tech_stack.md]
---

# Tech Stack Detection

A new developer needs to know, in 30 seconds, what a project is built with and what they need to learn. Your job is to produce a tech stack that is **evidence-based**: every item points to the file that proves it. Dependency lists lie (unused packages, leftover starters), so confirm with how the code actually uses things.

## Where to look

Check as many of these as exist, and reuse `onboarding/clone_report.json` and `onboarding/setup.json` if present:

- **Manifests and lockfiles**: `package.json`, `requirements.txt`, `pyproject.toml`, `pom.xml`, `build.gradle`, `go.mod`, `Cargo.toml`, `Gemfile`, `composer.json`, `*.csproj`. Take versions from lockfiles when available.
- **HTML and CDN imports**: `<script src>` and `<link>` tags. Some projects load React, Tailwind, Supabase, etc. from a CDN with no manifest entry, and those count.
- **Source imports**: what the code really imports and calls. Mark packages that are declared but never used.
- **Config files**: `vite.config.*`, `webpack.config.*`, `next.config.*`, `tsconfig.json`, `tailwind.config.*`, `.eslintrc*`, `babel.config.*`, `jest.config.*`, `vitest.config.*`, `playwright.config.*`.
- **Data layer**: ORM models, migrations, schema files, SQL dumps, connection strings (report the service type, never the credential), BaaS clients (Supabase, Firebase).
- **Infra and delivery**: `Dockerfile`, `docker-compose.yml`, `.github/workflows/`, `.gitlab-ci.yml`, `vercel.json`, `netlify.toml`, Terraform, Kubernetes manifests.
- **Env files**: variable names hint at external services (`STRIPE_KEY` → Stripe). Report names only.

## Categories

Use these categories. If a category is absent, say so explicitly ("No backend server: the browser talks to Supabase directly"), because "absent" is useful information:

languages · frontend · backend · database · auth · external services / APIs · state & data fetching · styling / UI · build & tooling · testing · linting / formatting · CI/CD · deployment / infra

## For each item record
- name and version (or "version unknown")
- the category
- **evidence**: file path (plus line if helpful)
- **confidence**: high (declared *and* used), medium (declared only, or used only), low (inferred from naming)
- a short note when useful, e.g. "declared in package.json but unused; app actually uses CDN React 17 in index.html"

## Output

Save to `onboarding/`:

- `onboarding/tech_stack.json`:
  ```json
  {
    "summary": "One-sentence description of the stack.",
    "categories": {
      "frontend": [{"name": "React", "version": "17.0.2", "evidence": ["index.html:11"], "confidence": "high", "note": "loaded via CDN"}],
      "backend": [],
      "database": [{"name": "Supabase (PostgreSQL)", "version": "unknown", "evidence": ["index.html:641", "database.txt"], "confidence": "high"}]
    },
    "absent": ["backend: no server code; browser calls Supabase directly"],
    "unused_dependencies": ["..."],
    "learning_list": {"Frontend": ["React hooks", "..."], "Backend": ["..."]}
  }
  ```
- `onboarding/tech_stack.md`: a clean table per category plus a short "what to learn first for each role" section. This file feeds the README and the dashboard.

Finish with a 3–5 line chat summary of the stack and the single most surprising finding.
