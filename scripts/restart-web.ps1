param(
    [int]$BackendPort = 7860,
    [int]$FrontendPort = 5173,
    [int]$BrowserDebugPort = 9222,
    [ValidateSet("preview", "dev")]
    [string]$FrontendMode = "preview",
    [switch]$NoOpenBrowser
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

function Find-InstalledChromiumBrowser {
    $candidates = @(
        (Join-Path $env:ProgramFiles "Google\Chrome\Application\chrome.exe"),
        (Join-Path ${env:ProgramFiles(x86)} "Google\Chrome\Application\chrome.exe"),
        (Join-Path $env:LOCALAPPDATA "Google\Chrome\Application\chrome.exe"),
        (Join-Path $env:ProgramFiles "Microsoft\Edge\Application\msedge.exe"),
        (Join-Path ${env:ProgramFiles(x86)} "Microsoft\Edge\Application\msedge.exe"),
        (Join-Path $env:LOCALAPPDATA "Microsoft\Edge\Application\msedge.exe")
    )

    foreach ($candidate in $candidates) {
        if ($candidate -and (Test-Path -LiteralPath $candidate)) {
            return $candidate
        }
    }

    return $null
}

function Find-PlaywrightChromium {
    param(
        [Parameter(Mandatory = $true)]
        [string]$ProjectRoot
    )

    $roots = @()
    if ($env:PLAYWRIGHT_BROWSERS_PATH -and $env:PLAYWRIGHT_BROWSERS_PATH -ne "0") {
        $roots += $env:PLAYWRIGHT_BROWSERS_PATH
    }
    $roots += @(
        (Join-Path $ProjectRoot ".venv\Lib\site-packages\playwright\driver\package\.local-browsers"),
        (Join-Path $ProjectRoot "web\node_modules\playwright-core\.local-browsers"),
        (Join-Path $env:LOCALAPPDATA "ms-playwright"),
        (Join-Path $env:USERPROFILE "AppData\Local\ms-playwright")
    )

    foreach ($root in $roots | Select-Object -Unique) {
        if (-not $root -or -not (Test-Path -LiteralPath $root)) {
            continue
        }
        $matches = @(
            Get-ChildItem -LiteralPath $root -Directory -Filter "chromium-*" -ErrorAction SilentlyContinue |
                ForEach-Object {
                    @(
                        (Join-Path $_.FullName "chrome-win64\chrome.exe"),
                        (Join-Path $_.FullName "chrome-win\chrome.exe")
                    )
                } |
                Where-Object { Test-Path -LiteralPath $_ }
        )
        if ($matches.Count -eq 0) {
            $matches = @(Get-ChildItem -LiteralPath $root -Recurse -Filter "chrome.exe" -ErrorAction SilentlyContinue |
                Where-Object { $_.FullName -match "[\\/]chromium-[^\\/]+[\\/]" } |
                Select-Object -ExpandProperty FullName)
        }
        $matches = @($matches | Sort-Object -Descending)
        if ($matches.Count -gt 0) {
            return $matches[0]
        }
    }

    return $null
}

function Find-InteractiveBrowser {
    param(
        [Parameter(Mandatory = $true)]
        [string]$ProjectRoot
    )

    $installed = Find-InstalledChromiumBrowser
    if ($installed) {
        return $installed
    }

    return Find-PlaywrightChromium -ProjectRoot $ProjectRoot
}

function Open-InternalBrowser {
    param(
        [Parameter(Mandatory = $true)]
        [string]$ProjectRoot,
        [Parameter(Mandatory = $true)]
        [string]$Url,
        [Parameter(Mandatory = $true)]
        [int]$DebugPort
    )

    $endpoint = "http://127.0.0.1:$DebugPort"
    try {
        Invoke-RestMethod -Uri "$endpoint/json/version" -TimeoutSec 1 | Out-Null
        $encodedUrl = [Uri]::EscapeDataString($Url)
        try {
            Invoke-RestMethod -Method Put -Uri "$endpoint/json/new?$encodedUrl" -TimeoutSec 2 | Out-Null
        } catch {
            Invoke-RestMethod -Uri "$endpoint/json/new?$encodedUrl" -TimeoutSec 2 | Out-Null
        }
        Write-Host "Opened a new tab in existing browser: $Url"
        return
    } catch {
        Write-Host "No existing browser found on debug port $DebugPort."
    }

    $browser = Find-InteractiveBrowser -ProjectRoot $ProjectRoot
    if (-not $browser) {
        Write-Warning "Chrome/Edge/Playwright Chromium was not found. Install Google Chrome or run: .\.venv\Scripts\python.exe -m playwright install chromium"
        return
    }

    $profileDir = Join-Path $ProjectRoot ".cache\publish-browser"
    New-Item -ItemType Directory -Force -Path $profileDir | Out-Null
    $arguments = @(
        "--remote-debugging-port=$DebugPort",
        "--user-data-dir=$profileDir",
        "--no-first-run",
        "--no-default-browser-check",
        $Url
    )

    Write-Host "Starting browser: $browser"
    Start-Process -FilePath $browser -ArgumentList $arguments -WorkingDirectory $ProjectRoot
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

if (-not $NoOpenBrowser) {
    Open-InternalBrowser -ProjectRoot $ProjectRoot -Url "http://127.0.0.1:$FrontendPort" -DebugPort $BrowserDebugPort
    Write-Host "Browser debug URL: http://127.0.0.1:$BrowserDebugPort"
}

Write-Host "Restart complete."
