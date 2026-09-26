---
name: readme-generator
description: Create a README.md if the repo has none, or audit an existing README for completeness and accuracy and fill in only what's missing or wrong — using the onboarding outputs (clone report, verified setup commands, tech stack, architecture). Use this whenever the user asks to write, fix, update, improve or check a README or project documentation, or as the final step of repo onboarding.
---

# README Generator

The README is the front door for every future developer. Your job is to make it complete and, above all, **true**: setup commands that actually work, a stack that matches the code, and an architecture that matches reality. You're working on someone else's project, so respect what's already there.

## Inputs

Use whatever exists in `onboarding/`, and treat it as your main source:
- `clone_report.json`: repo facts, license, warnings
- `setup.json` / `setup_report.md`: the **verified** install and run commands. Prefer these over anything written in an old README.
- `tech_stack.json` / `tech_stack.md`
- `architecture.md` / `architecture.mmd`
- `CODEBASE_MAP.md`

If some are missing, gather the facts directly from the code, and never write a command you haven't seen work or at least confirmed in the project's scripts. Mark unverified steps as "(not verified)".

## Decide which case applies

1. **No README** → create `README.md` from scratch.
2. **README exists but is incomplete or wrong** → update it:
   - Keep the author's existing content, tone and structure.
   - Add missing sections in sensible places.
   - Correct statements that are factually wrong (wrong commands, outdated stack), and note each correction in your report.
   - Don't delete sections unless they're clearly wrong, and say so if you do.
3. **README is complete and accurate** → change nothing. Report that it's complete and that the setup steps match the verified commands.

## What a complete README covers
Judge completeness against these, adapting to the project type (library, app, service, monorepo):

- project name and a one-paragraph description of what it does and who it's for
- key features
- tech stack (short list, or a table from tech_stack)
- architecture overview (embed the Mermaid diagram if available)
- prerequisites (runtimes and versions, accounts or services needed)
- installation and setup (the exact verified commands)
- environment variables (names, purpose, where to get them; never real values)
- how to run (dev, build, production)
- how to test (or "no tests yet")
- project structure (key folders and files)
- usage or demo accounts, if the project has them and they're meant to be public
- known issues and limitations
- contributing notes and license (write "No license specified" rather than inventing one)

## Output
- The created or updated `README.md` at the repo root.
- `onboarding/readme_report.md`: which case applied, sections added, corrections made (before → after), and anything left unverified.

Keep the README scannable: short sections, code blocks for commands, no marketing fluff. Never include secrets, personal data, or internal credentials.
