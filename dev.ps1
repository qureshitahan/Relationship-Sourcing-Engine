# Windows launcher: starts the FastAPI backend and the Vite frontend.
# Usage: .\dev.ps1      (Ctrl-C stops both)
#
# The bash dev.sh next to this file is Linux/macOS only — it uses .venv/bin/python
# and backgrounds with &, neither of which works here. This is the same launcher
# for Windows, plus three flags that exist because of problems this project hit:
#
#   --reload-delay 2
#       The reloader polls every 0.25s by default, so ONE logical edit that
#       touches several files (a change plus whatever the formatter rewrites)
#       fires several reloads in a row. The second reload's signal lands while
#       the worker from the first is still starting and kills it mid-startup —
#       that is the "KeyboardInterrupt ... CancelledError" pair in the log, and
#       sometimes no replacement worker comes up at all, leaving the port dead
#       and every page stuck on "Loading...". A wider window batches those file
#       writes into a single reload. Reloads land up to ~2s later; that is the
#       trade.
#
#   --timeout-graceful-shutdown 5
#       On reload, uvicorn waits for in-flight requests ("Waiting for background
#       tasks to complete"). With several browser tabs polling and the occasional
#       slow request, that stretches the window where the API is down. Five
#       seconds caps it.
#
#   --strictPort (frontend)
#       Vite silently takes the next free port when 5173 is busy, so a second
#       instance lands on 5174 and the browser tab you already had open points at
#       whichever one died. Fail loudly instead.

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Definition
$BackendDir = Join-Path $Root "backend"
$FrontendDir = Join-Path $Root "frontend"
$VenvPy = Join-Path $BackendDir ".venv\Scripts\python.exe"

# --- Pre-flight: a busy port is reported by Windows as WinError 10013 ("socket
# access forbidden"), which reads like a permissions problem and is not one.
foreach ($p in 8000, 5173) {
    $busy = Get-NetTCPConnection -State Listen -LocalPort $p -ErrorAction SilentlyContinue
    if ($busy) {
        $owner = Get-Process -Id $busy[0].OwningProcess -ErrorAction SilentlyContinue
        $name = if ($owner) { $owner.ProcessName } else { "unknown" }
        Write-Host "Port $p is already in use by PID $($busy[0].OwningProcess) ($name)." -ForegroundColor Yellow
        Write-Host "Stop it first:  Stop-Process -Id $($busy[0].OwningProcess) -Force" -ForegroundColor Yellow
        exit 1
    }
}

# --- Backend ---
if (-not (Test-Path $VenvPy)) {
    Write-Host "Creating backend virtualenv..."
    python -m venv (Join-Path $BackendDir ".venv")
    & $VenvPy -m pip install --quiet --upgrade pip
    & $VenvPy -m pip install --quiet -r (Join-Path $BackendDir "requirements.txt")
}

Write-Host "Starting backend on http://localhost:8000 ..." -ForegroundColor Cyan
$backend = Start-Process -FilePath $VenvPy -WorkingDirectory $BackendDir -PassThru -NoNewWindow `
    -ArgumentList @(
        "-m", "uvicorn", "app.main:app",
        "--port", "8000",
        "--reload",
        "--reload-delay", "2",
        "--timeout-graceful-shutdown", "5"
    )

# --- Frontend ---
if (-not (Test-Path (Join-Path $FrontendDir "node_modules"))) {
    Write-Host "Installing frontend dependencies..."
    Push-Location $FrontendDir
    npm install
    Pop-Location
}

Write-Host "Starting frontend on http://localhost:5173 ..." -ForegroundColor Cyan
try {
    Push-Location $FrontendDir
    npm run dev -- --strictPort
}
finally {
    Pop-Location
    if ($backend -and -not $backend.HasExited) {
        Write-Host "Stopping backend..."
        # /T kills the whole tree. --reload runs a reloader parent plus a worker
        # child; killing only the parent is what leaves an orphaned worker still
        # holding port 8000.
        & taskkill.exe /PID $backend.Id /T /F 2>&1 | Out-Null
    }
}
