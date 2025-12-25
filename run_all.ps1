# run_all.ps1
# Launch all services in separate PowerShell windows (including a periodic spot pipeline loop).
# Run from repo root:  .\run_all.ps1

$ErrorActionPreference = "Stop"

# --- CONFIG ---
$RepoRoot = (Get-Location).Path
$VenvActivate = Join-Path $RepoRoot ".venv\Scripts\Activate.ps1"
$ConfigPath = "config/config.yaml"

function Start-Runner {
    param(
        [string]$Title,
        [string]$RunArg
    )

    $cmd = @"
`$host.ui.RawUI.WindowTitle = '$Title'
Set-Location '$RepoRoot'
. '$VenvActivate'
python -m trader.main --config '$ConfigPath' --run $RunArg
"@

    Start-Process powershell -ArgumentList "-NoExit", "-Command", $cmd | Out-Null
    Start-Sleep -Milliseconds 350
}

function Start-LoopRunner {
    param(
        [string]$Title,
        [int]$EverySeconds
    )

    $cmd = @"
`$host.ui.RawUI.WindowTitle = '$Title'
Set-Location '$RepoRoot'
. '$VenvActivate'

while (`$true) {
    Write-Host "=== SPOT PIPELINE TICK: " (Get-Date) "==="
    python -m trader.main --config '$ConfigPath' --run spot_bars_1m
    python -m trader.main --config '$ConfigPath' --run spot_bars_5m
    python -m trader.main --config '$ConfigPath' --run spot_features_5m
    Start-Sleep -Seconds $EverySeconds
}
"@

    Start-Process powershell -ArgumentList "-NoExit", "-Command", $cmd | Out-Null
    Start-Sleep -Milliseconds 350
}

Write-Host "Launching runners from: $RepoRoot"
Write-Host "Using venv: $VenvActivate"
Write-Host ""

# --- Long-running streamers / pollers ---
Start-Runner -Title "01 SPOT_BBO"       -RunArg "spot_bbo"
Start-Runner -Title "02 PERP_FUNDING"   -RunArg "perp_funding"
Start-Runner -Title "03 BASIS_1M"       -RunArg "basis_1m"
Start-Runner -Title "04 BASELINE_SIG"   -RunArg "baseline_signals"

# --- Periodic spot pipeline builders (runs forever) ---
# Rebuild 1m bars -> 5m bars -> 5m features every N seconds
Start-LoopRunner -Title "05 SPOT_PIPELINE_LOOP" -EverySeconds 60

Start-Runner -Title "06 STATUS_WATCH"   -RunArg "status_watch"


Write-Host "Done. Close any window or press Ctrl+C inside it to stop that runner."
