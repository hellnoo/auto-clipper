# Auto-Clipper one-click launcher (Windows)
# Boots cobalt (Docker) + auto-clipper dashboard, opens browser, cleans up on exit.

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

# --- Banner ---
$OutputEncoding = [Text.UTF8Encoding]::new()
[Console]::OutputEncoding = [Text.UTF8Encoding]::new()
try { $Host.UI.RawUI.WindowTitle = "Auto-Clipper  ::  kanz x claude" } catch {}

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

Write-Host "          ╔══════════════════════════════════════════════════════════════╗" -ForegroundColor DarkCyan
Write-Host "          ║  " -NoNewline -ForegroundColor DarkCyan
Type-Char "viral short-form clipper" White
Write-Host "  ·  " -NoNewline -ForegroundColor DarkGray
Type-Char "yt-dlp + whisper + groq + ffmpeg" Gray 8
Write-Host "  ║" -ForegroundColor DarkCyan
Write-Host "          ╠══════════════════════════════════════════════════════════════╣" -ForegroundColor DarkCyan
Write-Host "          ║                                                              ║" -ForegroundColor DarkCyan
Write-Host "          ║" -NoNewline -ForegroundColor DarkCyan
Write-Host "                              " -NoNewline
Write-Host "» " -NoNewline -ForegroundColor DarkGray
Type-Char "kanz" Yellow 80
Write-Host " " -NoNewline
Write-Host "×" -NoNewline -ForegroundColor Magenta
Write-Host " " -NoNewline
Type-Char "claude" Cyan 80
Write-Host " " -NoNewline
Write-Host "«" -NoNewline -ForegroundColor DarkGray
Write-Host "                ║" -ForegroundColor DarkCyan
Write-Host "          ║                                                              ║" -ForegroundColor DarkCyan
Write-Host "          ╚══════════════════════════════════════════════════════════════╝" -ForegroundColor DarkCyan
Write-Host ""

# Glow flash on the "kanz x claude" line for that extra pop
Start-Sleep -Milliseconds 200
$brandY = [Console]::CursorTop - 3
$brandLine = "          ║                              » kanz × claude «                ║"
foreach ($flash in @('White','Yellow','Cyan','DarkCyan')) {
    [Console]::SetCursorPosition(0, $brandY)
    Write-Host $brandLine -ForegroundColor $flash -NoNewline
    Start-Sleep -Milliseconds 90
}
[Console]::SetCursorPosition(0, $brandY)
Write-Host "          ║                              " -NoNewline -ForegroundColor DarkCyan
Write-Host "» " -NoNewline -ForegroundColor DarkGray
Write-Host "kanz" -NoNewline -ForegroundColor Yellow
Write-Host " × " -NoNewline -ForegroundColor Magenta
Write-Host "claude" -NoNewline -ForegroundColor Cyan
Write-Host " «" -NoNewline -ForegroundColor DarkGray
Write-Host "                ║" -ForegroundColor DarkCyan
[Console]::SetCursorPosition(0, $brandY + 3)

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

# --- Speaker diarization (pyannote-audio) — opt-in via DIARIZE_ENABLED=1 ---
if ($dotenv["DIARIZE_ENABLED"] -eq "1") {
    $diarMarker = Join-Path $venv ".diar-installed"
    if (-not (Test-Path $diarMarker)) {
        Write-Host "  [..] Installing pyannote.audio for speaker diarization (~2 GB)..." -ForegroundColor Cyan
        & $py -m pip install --quiet "pyannote.audio>=3.1"
        Set-Content -Path $diarMarker -Value (Get-Date).ToString()
        Write-Host "  [ok] pyannote.audio installed" -ForegroundColor Green
    }
    if (-not $dotenv["HF_TOKEN"] -and -not $env:HF_TOKEN) {
        Write-Host "  [!] DIARIZE_ENABLED=1 but HF_TOKEN missing." -ForegroundColor Yellow
        Write-Host "      1. Token: https://huggingface.co/settings/tokens" -ForegroundColor Yellow
        Write-Host "      2. Accept terms at:" -ForegroundColor Yellow
        Write-Host "         - https://huggingface.co/pyannote/speaker-diarization-3.1" -ForegroundColor Yellow
        Write-Host "         - https://huggingface.co/pyannote/segmentation-3.0" -ForegroundColor Yellow
        Write-Host "      3. Add HF_TOKEN=hf_... to config\.env" -ForegroundColor Yellow
    }
}

# --- NVIDIA GPU acceleration for Whisper (one-time install) ---
$gpuMarker = Join-Path $venv ".gpu-checked"
if (-not (Test-Path $gpuMarker)) {
    $hasNvidia = $false
    if (Get-Command nvidia-smi -ErrorAction SilentlyContinue) { $hasNvidia = $true }
    if ($hasNvidia) {
        Write-Host "  [..] NVIDIA GPU detected — installing CUDA libs for Whisper (~400 MB)..." -ForegroundColor Cyan
        # ctranslate2 (the engine behind faster-whisper) needs cuDNN 9 + cuBLAS at runtime.
        & $py -m pip install --quiet "nvidia-cublas-cu12" "nvidia-cudnn-cu12==9.*"
        Write-Host "  [ok] GPU libs installed (Whisper will run on CUDA)" -ForegroundColor Green
    } else {
        Write-Host "  [info] no NVIDIA GPU detected — Whisper will run on CPU" -ForegroundColor DarkGray
    }
    Set-Content -Path $gpuMarker -Value (Get-Date).ToString()
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

# Read LLM_PROVIDER from config\.env so the rest of this script can pre-flight
# the right credentials. Important: do NOT export $env:LLM_PROVIDER here unless
# the user explicitly set it in their shell — otherwise it would override .env.
$envFile = Join-Path $PSScriptRoot "config\.env"
$dotenv = @{}
if (Test-Path $envFile) {
    foreach ($line in Get-Content $envFile) {
        if ($line -match "^\s*([A-Z_][A-Z0-9_]*)\s*=\s*(.*?)\s*$") {
            $dotenv[$Matches[1]] = $Matches[2].Trim().Trim('"').Trim("'")
        }
    }
}
$provider = if ($env:LLM_PROVIDER) { $env:LLM_PROVIDER } elseif ($dotenv["LLM_PROVIDER"]) { $dotenv["LLM_PROVIDER"] } else { "groq" }
$provider = $provider.ToLower()

Write-Host "  [ok] LLM provider: " -NoNewline -ForegroundColor Green
Write-Host $provider -ForegroundColor Cyan

if ($provider -eq "groq" -and -not $dotenv["GROQ_API_KEY"] -and -not $env:GROQ_API_KEY) {
    Write-Host ""
    Write-Host "  [!] GROQ_API_KEY not set." -ForegroundColor Yellow
    Write-Host "      Get a free key at https://console.groq.com" -ForegroundColor Yellow
    Write-Host "      Put 'GROQ_API_KEY=gsk_...' in config\.env then re-run." -ForegroundColor Yellow
    Read-Host "  Press Enter to exit"
    exit 1
}
if ($provider -eq "openrouter" -and -not $dotenv["OPENROUTER_API_KEY"] -and -not $env:OPENROUTER_API_KEY) {
    Write-Host ""
    Write-Host "  [!] OPENROUTER_API_KEY not set." -ForegroundColor Yellow
    Write-Host "      Get a key at https://openrouter.ai/keys" -ForegroundColor Yellow
    Write-Host "      Put 'OPENROUTER_API_KEY=sk-or-v1-...' in config\.env then re-run." -ForegroundColor Yellow
    Read-Host "  Press Enter to exit"
    exit 1
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
