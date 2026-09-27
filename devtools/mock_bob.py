"""Stand-in for the IBM Bob API, for local demos and tests while no Bob key is available.

It does NOT analyse code. Every file and answer it returns is labelled "stand-in", and only
reports facts about what the backend sent (file counts, folders, scanner results).

Run it next to the backend:
    uvicorn devtools.mock_bob:app --port 8765
    set BOB_CLIENT=http, BOB_API_KEY=any-value, BOB_API_ENDPOINT=http://127.0.0.1:8765,
        BOB_SKILLS_PATH=/inference/v1/skills/run   (in .env or the environment)
"""
import asyncio
import os

from fastapi import FastAPI, Request

app = FastAPI(title="Bob API stand-in (not real Bob)")
DELAY = float(os.getenv("MOCK_BOB_DELAY", "1.5"))
NOTE = "> **Stand-in output.** The real Bob API isn't connected yet, so this file only lists what Bob received."


@app.post("/inference/v1/skills/run")
async def run_skill(req: Request):
    body = await req.json()
    ctx = body.get("context", {})
    name = body.get("skill_name", "skill")
    repo = ctx.get("repo", {})
    files = [f["path"] for f in repo.get("files", [])]
    folders = sorted({p.split("/")[0] for p in files if "/" in p})
    tools = ctx.get("local_tool_results", {})
    findings = {
        spec.split()[0].replace("scripts/", ""): len(r["output"].get("findings", []))
        for spec, r in tools.items() if isinstance(r.get("output"), dict)
    }
    await asyncio.sleep(DELAY)

    facts = [
        f"- Files received: **{len(files)}** ({repo.get('total_chars', 0):,} characters)",
        f"- Top-level folders: {', '.join(f'`{f}`' for f in folders) or 'none'}",
        f"- Left out: {len(repo.get('omitted', []))} (binaries, lockfiles, secret files)",
        f"- Earlier results available: {', '.join(sorted(ctx.get('previous_results', {}))) or 'none'}",
    ] + [f"- `{tool}` findings (masked): **{n}**" for tool, n in findings.items()]
    md = f"# {name}\n\n{NOTE}\n\n" + "\n".join(facts) + "\n"

    def stand_in_json(path: str):
        """The shape the dashboard expects (orchestrator.OUTPUT_CONTRACTS), with placeholder values."""
        from orchestrator import OUTPUT_CONTRACTS
        blank = {str: "stand-in", int: 0, dict: {}, list: []}
        data = {key: blank[typ] for key, typ in OUTPUT_CONTRACTS.get(path, {}).items()}
        data.update({"stand_in": True, "skill": name, "files_received": len(files), "tool_findings": findings})
        if path.endswith("architecture.json"):
            data["nodes"] = [{"id": "stand-in", "label": "Stand-in", "layer": "core"}]
        return data

    out_files = {
        path: (stand_in_json(path) if path.endswith(".json") else
               "flowchart LR\n  A[Stand-in] --> B[Real Bob API not connected]\n" if path.endswith(".mmd") else md)
        for path in ctx.get("orchestrator", {}).get("expected_files", [])
    }
    user = ctx.get("user_input") or {}
    answer = None
    if name == "codebase-qa":
        answer = (f"**Stand-in answer.** Real answers need the Bob API.\n\n"
                  f"- Your role: {user.get('role') or 'not given'}\n"
                  f"- Your question: {user.get('question') or 'role briefing'}\n"
                  f"- Bob would read these files first:\n"
                  + "\n".join(f"  - `{p}`" for p in files[:6]))
    actions = [f"Review {n} masked finding(s) from {tool}" for tool, n in findings.items() if n]
    return {
        "summary": f"[stand-in] {name}: received {len(files)} files" + (f", {sum(findings.values())} scanner findings" if findings else ""),
        "files": out_files,
        "actions_for_user": actions,
        "answer": answer,
    }
