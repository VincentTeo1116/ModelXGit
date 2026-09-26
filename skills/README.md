# Model X Onboarding Skills for IBM Bob

Eight Bob skills for the onboarding pipeline. The backend (`main.py`) reads each
SKILL.md's frontmatter and runs them in this order:

| When | Skill | Waits for | Group | Does |
|------|-------|-----------|-------|------|
| User confirms the clone | repo-clone (`trigger: entry`) | - | - | Backend clones the repo; Bob writes the clone report |
| Automatically after repo-clone | setup-dependencies | - | setup | Install and run plan, env vars, API keys |
| | tech-stack-detection | - | analysis | Evidence-based tech stack |
| | architecture-diagram | tech-stack-detection | analysis | Layered architecture: Mermaid + JSON graph |
| | git-history-secret-audit | - | security | Secrets ever committed (runs `scripts/history_scan.py` locally) |
| | secret-precommit-scanner | - | security | Secrets in the current files (runs `scripts/precommit_scan.py` locally) |
| When the user selects it | codebase-qa | - | - | Role-based briefing + Q&A with file references |
| | readme-generator | tech-stack, architecture, setup | - | Creates or completes the README |

Auto skills run in parallel; a skill only waits for the skills in its `depends_on`.
`parallel_group` is a label for the dashboard.

## Frontmatter fields

    trigger: entry | auto | manual
    after: repo-clone              # auto skills: the event that starts them
    depends_on: [other-skill]      # waits for these to finish first
    parallel_group: analysis       # display label
    produces: [onboarding/x.json]  # files Bob returns; the backend saves them
    local_tools: [scripts/x.py]    # scripts the backend runs on the clone; results go to Bob

## Use directly in Bob IDE
Copy each skill folder into `.bob/skills/` in the project root
(or `~/.bob/skills/` to use them in every project).

## Shared convention
Every skill writes its results to an `onboarding/` folder in the target repo
(JSON for the dashboard, Markdown for humans), and later skills reuse earlier results.
