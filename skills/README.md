# Model X Onboarding Skills for IBM Bob

Six Bob skills for the onboarding pipeline:

| # | Skill | Does |
|---|-------|------|
| 1a | repo-clone | Clones the repo, records basic facts and risks |
| 1b | setup-dependencies | Installs dependencies, prepares .env, verifies it runs |
| 2 | codebase-qa | Role-based briefing + Q&A with file references |
| 3 | tech-stack-detection | Evidence-based tech stack |
| 4 | architecture-diagram | Layered architecture: Mermaid + JSON graph for the dashboard |
| 5 | readme-generator | Creates or completes the README from verified facts |

## Install
Copy each skill folder into `.bob/skills/` in the project root
(or `~/.bob/skills/` to use them in every project):

    .bob/skills/repo-clone/SKILL.md
    .bob/skills/setup-dependencies/SKILL.md
    ...

## Shared convention
Every skill writes its results to an `onboarding/` folder in the target repo
(JSON for the dashboard, Markdown for humans), and later skills reuse earlier results.
Suggested order: repo-clone -> setup-dependencies -> tech-stack-detection ->
architecture-diagram -> codebase-qa -> readme-generator.
