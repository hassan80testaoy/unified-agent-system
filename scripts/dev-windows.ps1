# Unified Agent System — Direct Windows Development Launcher (no Docker required)
# Launches FastAPI backend on http://127.0.0.1:8010 and Next.js frontend on http://127.0.0.1:3010

param(
    [switch]$InstallOnly,
    [int]$WebPort = 3010,
    [int]$ApiPort = 8010,
    [int]$BrowserPort = 7080
)

$ErrorActionPreference = "Stop"
$RootDir = Split-Path -Parent $PSScriptRoot
$BackendDir = Join-Path $RootDir "backend"
$BrowserWorkerDir = Join-Path $RootDir "browser-worker"
$WebDir = Join-Path $RootDir "web"
$EnvFile = Join-Path $RootDir ".env"
$EnvExample = Join-Path $RootDir ".env.example"

if (-not (Test-Path $EnvFile)) {
    Copy-Item $EnvExample $EnvFile
    Write-Host "[setup] Created .env from .env.example (edit secrets manually before production use)." -ForegroundColor Yellow
}

if (Test-Path $EnvFile) {
    foreach ($line in Get-Content $EnvFile) {
        if ($line -match '^\s*WEB_PORT=(\d+)' -and -not $PSBoundParameters.ContainsKey('WebPort')) {
            $WebPort = [int]$Matches[1]
        }
        if ($line -match '^\s*API_PORT=(\d+)' -and -not $PSBoundParameters.ContainsKey('ApiPort')) {
            $ApiPort = [int]$Matches[1]
        }
    }
}

$VenvDir = Join-Path $BackendDir ".venv"
$VenvPython = Join-Path $VenvDir "Scripts\python.exe"

if (-not (Test-Path $VenvPython)) {
    Write-Host "[backend] Creating Python virtual environment..." -ForegroundColor Cyan
    python -m venv $VenvDir
}

Write-Host "[backend] Installing Python dependencies..." -ForegroundColor Cyan
& $VenvPython -m pip install --upgrade pip
& $VenvPython -m pip install -r (Join-Path $BackendDir "requirements.txt")
if (Test-Path (Join-Path $BrowserWorkerDir "requirements.txt")) {
    & $VenvPython -m pip install -r (Join-Path $BrowserWorkerDir "requirements.txt")
}
$ChromiumReady = & $VenvPython -c "import os; from playwright.sync_api import sync_playwright; p = sync_playwright().start(); ex = os.path.exists(p.chromium.executable_path); p.stop(); print('1' if ex else '0')" 2>$null
if ($ChromiumReady -ne "1") {
    Write-Host "[browser-worker] Installing Playwright Chromium binary..." -ForegroundColor Cyan
    & $VenvPython -m playwright install chromium
}

Write-Host "[backend] Running Alembic database migrations..." -ForegroundColor Cyan
Push-Location $BackendDir
try {
    & $VenvPython -m alembic upgrade head
} finally {
    Pop-Location
}

Write-Host "[web] Installing Node dependencies..." -ForegroundColor Cyan
Push-Location $WebDir
try {
    if (-not (Test-Path (Join-Path $WebDir "node_modules"))) {
        cmd /c npm install
    }
} finally {
    Pop-Location
}

if ($InstallOnly) {
    Write-Host "[done] Installation and migrations complete." -ForegroundColor Green
    exit 0
}

$env:BACKEND_INTERNAL_URL = "http://127.0.0.1:$ApiPort"
$env:NEXT_PUBLIC_WS_API_PORT = "$ApiPort"
$env:API_PORT = "$ApiPort"
$env:BROWSER_WORKER_URL = "http://127.0.0.1:$BrowserPort"
$env:ARTIFACTS_DIR = (Join-Path $BackendDir "data\artifacts")
$env:PROFILES_DIR = (Join-Path $BackendDir "data\browser_profiles")
if (-not $env:BROWSER_HEADLESS) { $env:BROWSER_HEADLESS = "true" }

# Forward RESIDENTIAL_PROXY from .env into browser worker environment
if (Test-Path $EnvFile) {
    foreach ($line in Get-Content $EnvFile) {
        if ($line -match '^\s*RESIDENTIAL_PROXY=(.+)$' -and -not $env:RESIDENTIAL_PROXY) {
            $rp = $Matches[1].Trim()
            if ($rp) {
                $env:RESIDENTIAL_PROXY = $rp
                $env:BROWSER_PROXY_URL = $rp
            }
        }
    }
}

Write-Host "[launch] Starting Playwright Browser Worker (:$BrowserPort), Backend API (:$ApiPort), and Next.js Web (:$WebPort)..." -ForegroundColor Green
$BrowserJob = Start-Process -FilePath $VenvPython -ArgumentList "-m", "uvicorn", "interactive_worker:app", "--host", "127.0.0.1", "--port", "$BrowserPort" -WorkingDirectory $BrowserWorkerDir -PassThru
$ApiJob = Start-Process -FilePath $VenvPython -ArgumentList "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "$ApiPort", "--reload" -WorkingDirectory $BackendDir -PassThru
$WebJob = Start-Process -FilePath "node" -ArgumentList ".\node_modules\next\dist\bin\next", "dev", "-p", "$WebPort" -WorkingDirectory $WebDir -PassThru

Write-Host ""
Write-Host "Unified Agent System running locally:" -ForegroundColor Green
Write-Host "  Dashboard (RTL): http://127.0.0.1:$WebPort"
Write-Host "  API Health:      http://127.0.0.1:$ApiPort/api/health"
Write-Host "  Browser Worker:  http://127.0.0.1:$BrowserPort/interactive/health"
Write-Host "Press Ctrl+C or stop processes $($BrowserJob.Id), $($ApiJob.Id), and $($WebJob.Id) when done."
Wait-Process -Id $BrowserJob.Id, $ApiJob.Id, $WebJob.Id
