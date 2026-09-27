<#
  ModelXGit (Model X): one-command start on Windows.

    Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
    .\start.ps1                 # real Bob if .env has BOB_API_KEY, else the stand-in
    .\start.ps1 -StandIn        # always the labelled stand-in (no key needed)

  Then open http://127.0.0.1:8000/ (it opens by itself unless -NoBrowser).
#>
param([switch]$StandIn, [int]$Port = 8000, [switch]$NoBrowser)
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$bootstrapPython = (Get-Command python -ErrorAction SilentlyContinue).Source
if ($bootstrapPython) {
    & $bootstrapPython --version *> $null
    if ($LASTEXITCODE -ne 0) { $bootstrapPython = $null }
}
if (-not $bootstrapPython) {
    $bootstrapPython = (Get-Command py -ErrorAction SilentlyContinue).Source
}
if (-not $bootstrapPython) {
    throw "Python 3.10 or newer was not found. Install Python and reopen PowerShell."
}

if (-not (Test-Path ".venv\Scripts\python.exe")) {
    Write-Host "Creating .venv ..."
    & $bootstrapPython -m venv .venv
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path ".venv\Scripts\python.exe")) {
        throw "Could not create .venv. Check that your Python installation includes the venv module."
    }
}
$py = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
Write-Host "Installing requirements ..."
& $py -m pip install -q --disable-pip-version-check -r requirements.txt

$hasKey = (Test-Path ".env") -and (Select-String -Path ".env" -Pattern '^\s*BOB_API_KEY\s*=\s*(?!your_)\S' -Quiet)
$mock = $null
$set = @{}
if ($StandIn -or -not $hasKey) {
    Write-Host "Using the Bob STAND-IN (no real Bob calls). Put BOB_API_KEY in .env to use real IBM Bob." -ForegroundColor Yellow
    $set = @{ BOB_CLIENT = "http"; BOB_API_KEY = "stand-in"; BOB_API_ENDPOINT = "http://127.0.0.1:8765";  # secret-scan: allow (not a key)
              BOB_SKILLS_PATH = "/inference/v1/skills/run" }
    foreach ($k in $set.Keys) { Set-Item "env:$k" $set[$k] }
    $mock = Start-Process -FilePath $py -ArgumentList "-m", "uvicorn", "devtools.mock_bob:app", "--port", "8765", "--log-level", "warning" `
        -WorkingDirectory $PSScriptRoot -WindowStyle Hidden -PassThru
} else {
    if (-not (Get-Command bob -ErrorAction SilentlyContinue)) {
        Write-Host "Bob Shell not found. Install it with: npm install -g bobshell" -ForegroundColor Red
        exit 1
    }
    Write-Host "Using real IBM Bob through Bob Shell." -ForegroundColor Cyan
}

if (-not $NoBrowser) {
    Start-Job -ScriptBlock { param($p) Start-Sleep 3; Start-Process "http://127.0.0.1:$p/" } -ArgumentList $Port | Out-Null
}
Write-Host "ModelXGit on http://127.0.0.1:$Port/  (Ctrl+C to stop)" -ForegroundColor Green
try {
    & $py -m uvicorn main:app --port $Port
} finally {
    if ($mock) { Stop-Process -Id $mock.Id -Force -ErrorAction SilentlyContinue }
    foreach ($k in $set.Keys) { Remove-Item "env:$k" -ErrorAction SilentlyContinue }
}
