# Deploy ModelXGit on Render

This puts ModelXGit online with a link like `https://modelxgit.onrender.com`, protected by an
**access code** and **usage limits**, so nobody can use up the Bob key.

What runs on Render: one Docker container (`Dockerfile`) with the backend, the web app, git and
IBM Bob Shell, plus a 5 GB disk for clones and saved jobs. Everything is described in `render.yaml`.

**Cost:** Starter instance about $7/month + 5 GB disk about $1.25/month (Render prices). Bob usage is
billed to the Bob key, and the daily limits below cap it.

---

## 1. Before you start

You need:

- a **Render account** (render.com → Sign up, "Sign in with GitHub" is easiest);
- the **Bob API key** that will pay for the runs (bob.ibm.com → API keys);
- an **access code**: a long random password you will give to judges and teammates.
  Create one with:

  ```bash
  python -c "import secrets; print(secrets.token_urlsafe(12))"
  ```

## 2. Create the service

**Option A: Blueprint (one click, needs access to the repo on GitHub)**

Render reads `render.yaml` and sets up everything. This needs Render's GitHub app installed on the
account that owns `VincentTeo1116/ModelXGit` (the owner, or someone they give admin rights, can do it).

1. Render dashboard → **New** → **Blueprint**.
2. Connect GitHub and pick **ModelXGit**, branch **pipeline-frontend** (or `main` once it's merged).
3. Render asks for the three secret values (see step 3) → **Apply**.

**Option B: from the public repo link (no GitHub access needed)**

1. Render dashboard → **New** → **Web Service** → **Public Git Repository**.
2. URL: `https://github.com/VincentTeo1116/ModelXGit` → branch **pipeline-frontend**.
3. **Language:** Docker. **Region:** Virginia (US East). **Instance type:** Starter.
4. **Advanced** → **Add Disk**: name `modelxgit-data`, mount path `/data`, size 5 GB.
5. **Health Check Path:** `/api/health`.
6. Add every environment variable from step 3 (secrets and settings).
7. **Deploy Web Service.**

With option B, Render doesn't redeploy by itself on a push: use **Manual Deploy → Deploy latest commit**.

## 3. Environment variables

Secrets: type them into Render only. Never put them in git, chat or screenshots.

| Variable | Value |
|---|---|
| `BOB_API_KEY` | the Bob API key |
| `ACCESS_CODE` | your access code from step 1 |
| `BOB_ACCEPT_LICENSE` | `true` only if the key's owner has read and accepted the IBM Bob Shell license (`bob --show-license`) |
| `SESSION_SECRET` | any long random value (the Blueprint creates one for you) |

Settings (the Blueprint sets these; with option B add them yourself):

| Variable | Value | What it does |
|---|---|---|
| `BOB_CLIENT` | `shell` | use IBM Bob Shell |
| `WORKSPACE_DIR` | `/data/workspace` | keep clones and jobs on the disk |
| `MAX_PARALLEL_SKILLS` | `1` | one Bob run at a time (fits Starter's 512 MB; use `3` on a 2 GB plan) |
| `MAX_ONBOARDINGS_PER_DAY` | `15` | new onboardings per day, for everyone together |
| `MAX_SKILL_RUNS_PER_DAY` | `60` | questions, README runs and retries per day |
| `MAX_ACTIVE_ONBOARDINGS` | `1` | onboardings running at the same time |
| `MAX_REPO_MB` | `200` | bigger repositories are refused |
| `MAX_STORED_JOBS` | `40` | the oldest finished onboardings are deleted after this |
| `ALLOWED_GIT_HOSTS` | `github.com` | only GitHub links |

Days are UTC. `0` means no limit.

## 4. Check it works

The first build takes about 5 minutes. Then:

1. Open `https://<your-service>.onrender.com/api/health`. You should see `"bob_configured": true`
   and `"hosted": true`.
2. Open `https://<your-service>.onrender.com/`. You should get the **sign-in** page. Enter the access code.
3. Onboard `https://github.com/octocat/Hello-World` (small and quick). All steps should turn green.

If the Bob steps fail:

| Error | Fix |
|---|---|
| "license" | set `BOB_ACCEPT_LICENSE=true` (after accepting the license) and redeploy |
| "BOB_API_KEY is not set" or "unauthorized" | check the key in Render → Environment |
| "HTTP 403" or a Cloudflare page | IBM Bob blocks requests from this server. Tell the team: this is not something to work around |
| the service restarts during a run ("out of memory") | keep `MAX_PARALLEL_SKILLS=1`, or move to a 2 GB plan |

Logs: Render dashboard → the service → **Logs**.

## 5. Share it

Give judges and teammates the **link and the access code** through the hackathon submission form
or a private message. Don't put the code in the README or on a public page.

- **Change the access code:** edit `ACCESS_CODE` in Render → Environment. Everyone who signed in with
  the old code is signed out.
- **If the Bob key leaks:** delete it at bob.ibm.com, create a new one, and update `BOB_API_KEY`.
- **Pause it:** Render → the service → **Settings → Suspend**. Nothing runs or bills for compute
  while it's suspended.

## 6. What's different online

- The **access code** is asked once per browser and lasts 7 days (`SESSION_DAYS`).
- **Open in Bob IDE** only works when ModelXGit runs on your own computer. Online, download the pack.
- The **limits** are shown under the repository box and in **Settings**.
- A restart during an onboarding marks its running steps "interrupted"; use **Retry failed**.

## Run the container yourself (optional)

```bash
docker build -t modelxgit .
docker run -p 8000:8000 --env-file .env -v modelxgit-data:/data modelxgit
```
