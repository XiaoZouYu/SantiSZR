param(
    [int]$BackendPort = 7860,
    [int]$FrontendPort = 5173,
    [ValidateSet("preview", "dev")]
    [string]$FrontendMode = "preview"
)

$ErrorActionPreference = "Stop"

function Stop-PortProcess {
    param(
        [Parameter(Mandatory = $true)]
        [int]$Port,
        [Parameter(Mandatory = $true)]
        [string]$Name
    )

    $processIds = @(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
        Select-Object -ExpandProperty OwningProcess -Unique)

    if ($processIds.Count -eq 0) {
        Write-Host "$Name is not listening on port $Port."
        return
    }

    foreach ($processId in $processIds) {
        if ($processId -eq $PID) {
            continue
        }
        Write-Host "Stopping $Name process $processId on port $Port..."
        Stop-Process -Id $processId -Force -ErrorAction SilentlyContinue
    }

    $deadline = (Get-Date).AddSeconds(20)
    do {
        Start-Sleep -Milliseconds 300
        $stillListening = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
    } while ($stillListening -and (Get-Date) -lt $deadline)
}

function Wait-Port {
    param(
        [Parameter(Mandatory = $true)]
        [int]$Port,
        [Parameter(Mandatory = $true)]
        [string]$Name,
        [int]$TimeoutSeconds = 30
    )

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    do {
        $listener = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
            Select-Object -First 1
        if ($listener) {
            Write-Host "$Name is listening on port $Port. PID: $($listener.OwningProcess)"
            return $true
        }
        Start-Sleep -Milliseconds 500
    } while ((Get-Date) -lt $deadline)

    Write-Warning "$Name did not start listening on port $Port within $TimeoutSeconds seconds."
    return $false
}

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Resolve-Path (Join-Path $ScriptDir "..")
$FrontendDir = Join-Path $ProjectRoot "web"
$LogDir = Join-Path $ProjectRoot "data\logs"
$SrcDir = Join-Path $ProjectRoot "src"
$VenvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"

New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

$BackendOut = Join-Path $LogDir "web-backend.out.log"
$BackendErr = Join-Path $LogDir "web-backend.err.log"
$FrontendOut = Join-Path $LogDir "web-frontend.out.log"
$FrontendErr = Join-Path $LogDir "web-frontend.err.log"
$FrontendBuildOut = Join-Path $LogDir "web-frontend-build.out.log"
$FrontendBuildErr = Join-Path $LogDir "web-frontend-build.err.log"

Write-Host "Project: $ProjectRoot"
Write-Host "Restarting backend and frontend..."

Stop-PortProcess -Port $BackendPort -Name "Backend"
Stop-PortProcess -Port $FrontendPort -Name "Frontend"

$Python = if (Test-Path -LiteralPath $VenvPython) { $VenvPython } else { "python" }
$env:PYTHONPATH = $SrcDir

Write-Host "Starting backend: $Python -m santiszr.web --host 0.0.0.0 --port $BackendPort"
$BackendProcess = Start-Process `
    -FilePath $Python `
    -ArgumentList @("-m", "santiszr.web", "--host", "0.0.0.0", "--port", "$BackendPort") `
    -WorkingDirectory $ProjectRoot `
    -WindowStyle Hidden `
    -RedirectStandardOutput $BackendOut `
    -RedirectStandardError $BackendErr `
    -PassThru

if ($FrontendMode -eq "preview") {
    Write-Host "Building frontend for production preview: npm run build"
    $BuildProcess = Start-Process `
        -FilePath "cmd.exe" `
        -ArgumentList @("/c", "npm run build") `
        -WorkingDirectory $FrontendDir `
        -WindowStyle Hidden `
        -RedirectStandardOutput $FrontendBuildOut `
        -RedirectStandardError $FrontendBuildErr `
        -PassThru `
        -Wait

    if ($BuildProcess.ExitCode -ne 0) {
        Write-Host "Frontend build failed. Check logs:"
        Write-Host "  $FrontendBuildOut"
        Write-Host "  $FrontendBuildErr"
        exit $BuildProcess.ExitCode
    }

    Write-Host "Starting frontend: npm run preview -- --host 0.0.0.0 --port $FrontendPort"
    $FrontendProcess = Start-Process `
        -FilePath "cmd.exe" `
        -ArgumentList @("/c", "npm run preview -- --host 0.0.0.0 --port $FrontendPort") `
        -WorkingDirectory $FrontendDir `
        -WindowStyle Hidden `
        -RedirectStandardOutput $FrontendOut `
        -RedirectStandardError $FrontendErr `
        -PassThru
} else {
    Remove-Item -Recurse -Force (Join-Path $FrontendDir "node_modules\.vite") -ErrorAction SilentlyContinue
    Write-Host "Starting frontend: npm run dev -- --host 0.0.0.0 --port $FrontendPort"
    $FrontendProcess = Start-Process `
        -FilePath "cmd.exe" `
        -ArgumentList @("/c", "npm run dev -- --host 0.0.0.0 --port $FrontendPort") `
        -WorkingDirectory $FrontendDir `
        -WindowStyle Hidden `
        -RedirectStandardOutput $FrontendOut `
        -RedirectStandardError $FrontendErr `
        -PassThru
}

$BackendReady = Wait-Port -Port $BackendPort -Name "Backend"
$FrontendReady = Wait-Port -Port $FrontendPort -Name "Frontend"

Write-Host ""
Write-Host "Backend PID launcher: $($BackendProcess.Id)"
Write-Host "Frontend PID launcher: $($FrontendProcess.Id)"
Write-Host "Backend URL:  http://127.0.0.1:$BackendPort"
Write-Host "Frontend URL: http://127.0.0.1:$FrontendPort"
Write-Host "Frontend mode: $FrontendMode"
Write-Host "Backend logs:  $BackendOut"
Write-Host "Frontend logs: $FrontendOut"
if ($FrontendMode -eq "preview") {
    Write-Host "Frontend build logs: $FrontendBuildOut"
}

if (-not ($BackendReady -and $FrontendReady)) {
    Write-Host ""
    Write-Host "Startup was not fully ready. Check logs:"
    Write-Host "  $BackendErr"
    Write-Host "  $FrontendErr"
    exit 1
}

try {
    $health = Invoke-RestMethod -Uri "http://127.0.0.1:$BackendPort/api/health" -TimeoutSec 8
    Write-Host "Backend health: $($health.app) $($health.version)"
} catch {
    Write-Warning "Backend health check failed: $($_.Exception.Message)"
}

Write-Host "Restart complete."
