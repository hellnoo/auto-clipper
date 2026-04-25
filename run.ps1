# Auto-Clipper one-click launcher (Windows)
# Boots cobalt (Docker) + auto-clipper dashboard, opens browser, cleans up on exit.

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

# --- Banner ---
$OutputEncoding = [Text.UTF8Encoding]::new()
[Console]::OutputEncoding = [Text.UTF8Encoding]::new()
try { $Host.UI.RawUI.WindowTitle = "Auto-Clipper  -  by uncle_w" } catch {}

$banner = @(
    "   █████╗ ██╗   ██╗████████╗ ██████╗      ██████╗██╗     ██╗██████╗ ██████╗ ███████╗██████╗",
    "  ██╔══██╗██║   ██║╚══██╔══╝██╔═══██╗    ██╔════╝██║     ██║██╔══██╗██╔══██╗██╔════╝██╔══██╗",
    "  ███████║██║   ██║   ██║   ██║   ██║    ██║     ██║     ██║██████╔╝██████╔╝█████╗  ██████╔╝",
    "  ██╔══██║██║   ██║   ██║   ██║   ██║    ██║     ██║     ██║██╔═══╝ ██╔═══╝ ██╔══╝  ██╔══██╗",
    "  ██║  ██║╚██████╔╝   ██║   ╚██████╔╝    ╚██████╗███████╗██║██║     ██║     ███████╗██║  ██║",
    "  ╚═╝  ╚═╝ ╚═════╝    ╚═╝    ╚═════╝      ╚═════╝╚══════╝╚═╝╚═╝     ╚═╝     ╚══════╝╚═╝  ╚═╝"
)
$settled = @('Cyan','Cyan','DarkCyan','Magenta','DarkMagenta','DarkMagenta')

Write-Host ""

# Phase 1: type-in line by line, dim
$startY = [Console]::CursorTop
foreach ($line in $banner) {
    Write-Host $line -ForegroundColor DarkGray
    Start-Sleep -Milliseconds 50
}

# Phase 2: CRT scan - bright white sweeps top to bottom 2x
for ($pass = 0; $pass -lt 2; $pass++) {
    for ($scan = 0; $scan -lt $banner.Count; $scan++) {
        for ($i = 0; $i -lt $banner.Count; $i++) {
            [Console]::SetCursorPosition(0, $startY + $i)
            $color = if ($i -eq $scan) { 'White' }
                     elseif ($i -eq $scan - 1) { 'Cyan' }
                     elseif ($i -eq $scan + 1) { 'Cyan' }
                     else { 'DarkGray' }
            Write-Host $banner[$i] -ForegroundColor $color -NoNewline
        }
        Start-Sleep -Milliseconds 55
    }
}

# Phase 3: settle into cyan→magenta gradient
for ($i = 0; $i -lt $banner.Count; $i++) {
    [Console]::SetCursorPosition(0, $startY + $i)
    Write-Host $banner[$i] -ForegroundColor $settled[$i] -NoNewline
}
[Console]::SetCursorPosition(0, $startY + $banner.Count)
Write-Host ""

# Phase 4: typewriter tagline
function Type-Char($text, $color, $delay = 12) {
    foreach ($c in $text.ToCharArray()) {
        Write-Host -NoNewline $c -ForegroundColor $color
        Start-Sleep -Milliseconds $delay
    }
}

Write-Host "          ┌──────────────────────────────────────────────────────────────┐" -ForegroundColor DarkGray
Write-Host "          │  " -NoNewline -ForegroundColor DarkGray
Type-Char "viral short-form clipper" White
Write-Host "  ·  " -NoNewline -ForegroundColor DarkGray
Type-Char "yt-dlp + whisper + groq + ffmpeg" Gray 8
Write-Host "  │" -ForegroundColor DarkGray
Write-Host "          │" -NoNewline -ForegroundColor DarkGray
Write-Host "                                                  by " -NoNewline -ForegroundColor DarkGray
Type-Char "uncle_w" Yellow 60
Write-Host "  🎬  │" -ForegroundColor DarkGray
Write-Host "          └──────────────────────────────────────────────────────────────┘" -ForegroundColor DarkGray
Write-Host ""

# --- Pre-flight checks ---
function Need($cmd, $hint) {
    if (-not (Get-Command $cmd -ErrorAction SilentlyContinue)) {
        Write-Host "  [x] '$cmd' not found." -ForegroundColor Red
        Write-Host "      $hint" -ForegroundColor Yellow
        Read-Host "  Press Enter to exit"
        exit 1
    }
}
Need "python"  "Install Python 3.11+ from https://www.python.org/downloads/  (check 'Add to PATH')"
Need "ffmpeg"  "Install: winget install Gyan.FFmpeg   (then restart this window)"
Need "docker"  "Install Docker Desktop: https://www.docker.com/products/docker-desktop/"

Write-Host "  [ok] python, ffmpeg, docker found" -ForegroundColor Green

# --- venv + deps ---
$venv = Join-Path $PSScriptRoot ".venv"
if (-not (Test-Path "$venv\Scripts\python.exe")) {
    Write-Host "  [..] Creating virtualenv (.venv)..." -ForegroundColor Cyan
    python -m venv .venv
}
$py = "$venv\Scripts\python.exe"

# Marker file so we don't reinstall on every launch.
$marker = Join-Path $venv ".deps-installed"
$reqHash = (Get-FileHash requirements.txt -Algorithm MD5).Hash
$haveHash = if (Test-Path $marker) { Get-Content $marker } else { "" }
if ($haveHash -ne $reqHash) {
    Write-Host "  [..] Installing Python dependencies (one-time, ~3 min)..." -ForegroundColor Cyan
    & $py -m pip install --upgrade pip --quiet
    & $py -m pip install -r requirements.txt --quiet
    Set-Content -Path $marker -Value $reqHash
}

# --- cobalt ---
$cobaltRunning = (docker ps --filter "name=cobalt" --filter "status=running" --format "{{.Names}}" 2>$null) -eq "cobalt"
if (-not $cobaltRunning) {
    Write-Host "  [..] Starting cobalt (YouTube downloader)..." -ForegroundColor Cyan
    docker rm -f cobalt 2>$null | Out-Null
    docker run -d --name cobalt --restart unless-stopped -p 9000:9000 `
        -e API_URL="http://localhost:9000/" `
        -e API_PORT=9000 `
        -e CORS_WILDCARD=1 `
        ghcr.io/imputnet/cobalt:10 | Out-Null
    Start-Sleep -Seconds 3
}
Write-Host "  [ok] cobalt running on http://localhost:9000" -ForegroundColor Green

# --- env for the app ---
$env:COBALT_API_URL = "http://localhost:9000"
if (-not $env:LLM_PROVIDER) { $env:LLM_PROVIDER = "groq" }

if ($env:LLM_PROVIDER -eq "groq" -and -not $env:GROQ_API_KEY) {
    $envFile = Join-Path $PSScriptRoot "config\.env"
    if (Test-Path $envFile) {
        Get-Content $envFile | Where-Object { $_ -match "^\s*GROQ_API_KEY\s*=" } | ForEach-Object {
            $env:GROQ_API_KEY = ($_ -split "=", 2)[1].Trim().Trim('"')
        }
    }
    if (-not $env:GROQ_API_KEY) {
        Write-Host ""
        Write-Host "  [!] GROQ_API_KEY not set." -ForegroundColor Yellow
        Write-Host "      Get a free key at https://console.groq.com" -ForegroundColor Yellow
        Write-Host "      Then either:" -ForegroundColor Yellow
        Write-Host "        - put 'GROQ_API_KEY=gsk_...' in config\.env" -ForegroundColor Yellow
        Write-Host "        - or set `$env:GROQ_API_KEY before running this script" -ForegroundColor Yellow
        Write-Host "      (or set LLM_PROVIDER=ollama if you have Ollama installed)" -ForegroundColor Yellow
        Write-Host ""
        Read-Host "  Press Enter to exit"
        exit 1
    }
}

# --- launch dashboard ---
$port = 8000
Write-Host ""
Write-Host "  Starting dashboard at http://localhost:$port ..." -ForegroundColor Cyan
Write-Host "  (Press Ctrl+C to stop)" -ForegroundColor DarkGray
Write-Host ""

# Open browser shortly after server starts
Start-Job -ScriptBlock {
    param($p)
    Start-Sleep -Seconds 2
    Start-Process "http://localhost:$p"
} -ArgumentList $port | Out-Null

try {
    & $py -m uvicorn dashboard.app:app --host 127.0.0.1 --port $port
} finally {
    Write-Host ""
    Write-Host "  Stopping..." -ForegroundColor DarkGray
    # Cobalt left running so next launch is instant. To stop it:  docker stop cobalt
}
