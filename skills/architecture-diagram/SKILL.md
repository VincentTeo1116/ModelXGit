---
name: architecture-diagram
description: Analyze a repository and generate its system architecture — layers (Client/Frontend, API/Gateway, Core Services/Business Logic, Database/Storage, External Third-Party APIs), components, and how they connect — as a Mermaid diagram plus a JSON graph for an interactive dashboard with drill-down to files and endpoints. Use this whenever the user asks for an architecture diagram, system design, "how do the parts connect", module/dependency map, or during repo onboarding.
trigger: auto
after: repo-clone
depends_on: [tech-stack-detection]
parallel_group: analysis
produces: [onboarding/architecture.json, onboarding/architecture.mmd, onboarding/architecture.md]
---

# Architecture Diagram

Your goal is the diagram a senior engineer would draw on a whiteboard for a new hire: the real building blocks, how data moves between them, and where each block lives in the code. Every node and edge must come from evidence in the repo. A clean, honest diagram with 8 accurate boxes beats a busy one with 30 guessed ones.

## How to analyse

Reuse `onboarding/tech_stack.json` and `onboarding/CODEBASE_MAP.md` if they exist. Then look at:

- **Directory layout**: how the code is organised (by feature, layer, service, package).
- **Entry points**: `main`/`index` files, `ReactDOM.render`/`createRoot`, server bootstraps, CLI entry points, Dockerfile `CMD`.
- **Imports and calls**: who calls whom. Follow imports between modules, and for single-file apps follow the functions and components.
- **Routes and endpoints**: backend route definitions (Express, FastAPI, Spring, Django, Next API routes...), and frontend routes (router config, or `window.location` navigation).
- **Config and infra**: `docker-compose.yml` services, ports, database schemas and migrations, API client setup, env variable names, CI deploy targets.
- **External calls**: SDK clients and HTTP calls to third-party services (Supabase, Stripe, OpenAI, S3...).

## Layers

Place components into these five layers:

1. **Client / Frontend**: pages, main components, client state
2. **API / Gateway**: HTTP endpoints, routers, controllers, GraphQL, MCP servers
3. **Core Services / Business Logic**: services, domain logic, jobs, schedulers
4. **Database / Storage**: databases, tables or models, caches, file and blob storage, browser storage when it's used as a data store
5. **External Third-Party APIs**: outside services the system depends on

If a layer doesn't exist, keep it in the output as **absent** with a one-line explanation (for example "No API layer: the frontend calls the hosted database directly through one data-access module"). That is one of the most useful facts for a newcomer.

Group sensibly: aim for roughly 5–20 nodes. Collapse many small files into one meaningful component and list the files inside it.

## Output

Save to `onboarding/`:

- `onboarding/architecture.json`, which the dashboard renders as an interactive graph where clicking a node shows its files and endpoints (the values below are only an example of the shape):
  ```json
  {
    "summary": "2-3 sentence description of the architecture.",
    "layers": [
      {"id": "client", "name": "Client / Frontend", "present": true},
      {"id": "api", "name": "API / Gateway", "present": false, "note": "why it is absent, in one line"}
    ],
    "nodes": [
      {
        "id": "orders-page",
        "label": "Orders Page",
        "layer": "client",
        "description": "What it does in one line.",
        "files": [{"path": "src/pages/Orders.tsx", "lines": "1-180"}],
        "endpoints": [],
        "exports": ["OrdersPage"]
      }
    ],
    "edges": [
      {"from": "orders-page", "to": "orders-service", "type": "calls | reads | writes | navigates | depends_on", "label": "listOrders()"}
    ]
  }
  ```
  Keep node `id`s stable and kebab-case so other tools can link to them.
- `onboarding/architecture.mmd`: a Mermaid `flowchart` with one `subgraph` per present layer and labelled arrows. Keep labels short so it renders cleanly.
- `onboarding/architecture.md`: the Mermaid diagram embedded in a ```mermaid block, plus a short walkthrough of 2–3 key request or data flows (e.g. "a user places an order") that follow the arrows.

Check before finishing: every node has at least one real file, every edge is backed by an actual import or call, and the JSON is valid.
