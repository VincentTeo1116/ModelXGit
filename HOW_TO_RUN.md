# How to run ModelXGit (Model X)

ModelXGit onboards any GitHub repo with IBM Bob. Paste a repo link, Bob analyses it (setup, tech
stack, architecture, secret audits), then you can ask it questions and generate a README.

You can run it in two ways:
- **Demo mode (no Bob key):** everything works, but Bob's answers are clearly labelled placeholders.
- **Real Bob mode:** real analysis by IBM Bob. You need your own Bob API key.

---

## 1. What to download

| What | Needed for | Where |
|---|---|---|
| **Git** | always | https://git-scm.com/downloads |
| **Python 3.10 or newer** | always | https://www.python.org/downloads/ (on Windows tick "Add python.exe to PATH") |
| **The project** | always | see step 2 |
| **Node.js 22.15 or newer** | real Bob mode only | https://nodejs.org/ |
| **Bob Shell** | real Bob mode only | after Node 22.15+ is installed. Windows: `irm https://bob.ibm.com/download/bobshell.ps1 \| iex` · Mac/Linux: `curl -fsSL https://bob.ibm.com/download/bobshell.sh \| bash` |
| **IBM Bob IDE** | optional ("Open in Bob IDE" button) | from the hackathon / https://bob.ibm.com |

Check what you have:
```
git --version
python --version
node -v          (only for real Bob mode)
```

## 2. Get the project

```
git clone -b pipeline-frontend https://github.com/VincentTeo1116/ModelXGit.git
cd ModelXGit
```
No Git? Download the zip instead:
https://github.com/VincentTeo1116/ModelXGit/archive/refs/heads/pipeline-frontend.zip
and unzip it.

## 3. Start it (one command)

**Windows** (in PowerShell, inside the ModelXGit folder):
```
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\start.ps1
```

**Mac / Linux** (in a terminal, inside the ModelXGit folder):
```
chmod +x start.sh
./start.sh
```

The first start takes a minute (it creates `.venv` and installs the Python packages).
Your browser opens **http://127.0.0.1:8000/** by itself. Press **Ctrl+C** in the terminal to stop.

Without a Bob key it starts in **demo mode** automatically.

## 4. Use real IBM Bob (optional)

1. Get an API key: sign in at https://bob.ibm.com, open your subscription's **API keys**, click **Create**,
   and copy the key (it's shown once).
2. Read the Bob Shell license: `bob --show-license`
3. In the ModelXGit folder, create a file named **`.env`** containing:
   ```
   BOB_CLIENT=shell
   BOB_API_KEY=paste-your-key-here
   BOB_ACCEPT_LICENSE=true
   ```
   Only set `BOB_ACCEPT_LICENSE=true` if you accept the license. If your key type is "general",
   also add `BOB_TEAM_ID=your-team-id`.
4. Start again (step 3). The sidebar should say **"Bob connected"**.

Real Bob costs money on your Bob account: one full onboarding of a small repo is about 3–6 in
Bob's cost units and takes about 4–8 minutes.

## 5. Using the app

1. **Overview:** paste a public repo link (e.g. `https://github.com/octocat/Hello-World`),
   click **Clone repository**, then **Clone and run skills** to confirm.
2. **Repo page:** watch the pipeline. When it's done you get:
   - **Impact:** time to onboard, files produced, secret findings, Bob usage
   - **Ask Bob about this repo:** pick your role and ask anything; click Bob's suggested follow-ups
   - **README:** generate one once the needed skills have finished
   - **Architecture:** click a component to see its files and connections
   - **Onboarding files:** read every report, or **Download pack (.zip)**
   - **Continue in Bob IDE:** opens the clone in Bob IDE with the same skills
3. If a step fails, click **Retry failed**.

## 6. If something goes wrong

| Problem | Fix |
|---|---|
| `python` is not recognized | The launcher falls back to `py`; if both commands are unavailable, reinstall Python with "Add python.exe to PATH" ticked, then open a new terminal |
| "running scripts is disabled" (Windows) | Run `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass`, then retry `.\start.ps1` |
| Port 8000 is busy | Windows: `... start.ps1 -Port 8010` · Mac/Linux: `PORT=8010 ./start.sh` |
| "Bob Shell not found" | Install Node.js 22.15+, then Bob Shell (Windows: `irm https://bob.ibm.com/download/bobshell.ps1 \| iex` · Mac/Linux: `curl -fsSL https://bob.ibm.com/download/bobshell.sh \| bash`), and open a new terminal |
| "Bob Shell needs the IBM license accepted" | Add `BOB_ACCEPT_LICENSE=true` to `.env` (after reading the license) |
| "repository not found, or it is private" | Only public repos are supported |
| Steps show "Bob key missing" | Check `.env` is in the ModelXGit folder and has `BOB_API_KEY=` |

## 7. Put it online

See [DEPLOY.md](DEPLOY.md): Render, with an access code and daily limits.

## 8. Keep your key safe

- **Never** send your Bob key in chat, email or screenshots, and never commit `.env`
  (Git already ignores it).
- If a key was shared by mistake, delete it at bob.ibm.com and create a new one.
