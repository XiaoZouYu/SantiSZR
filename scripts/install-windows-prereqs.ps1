param(
    [switch]$SkipProjectSetup,
    [switch]$SkipFrontendBuild,
    [switch]$SkipPlaywright,
    [switch]$NoElevate
)

$ErrorActionPreference = "Stop"

function Test-IsAdministrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Invoke-SelfElevate {
    if ($NoElevate -or (Test-IsAdministrator)) {
        return
    }

    $arguments = @(
        "-NoProfile",
        "-ExecutionPolicy", "Bypass",
        "-File", "`"$PSCommandPath`""
    )
    if ($SkipProjectSetup) { $arguments += "-SkipProjectSetup" }
    if ($SkipFrontendBuild) { $arguments += "-SkipFrontendBuild" }
    if ($SkipPlaywright) { $arguments += "-SkipPlaywright" }
    $arguments += "-NoElevate"

    Write-Host "Administrator permission is required for system installers. Re-launching..."
    Start-Process -FilePath "powershell.exe" -ArgumentList $arguments -Verb RunAs
    exit
}

function Get-CommandPath {
    param([Parameter(Mandatory = $true)][string]$Name)
    $command = Get-Command $Name -ErrorAction SilentlyContinue
    if ($command) {
        return $command.Source
    }
    return $null
}

function Add-PathIfExists {
    param([Parameter(Mandatory = $true)][string]$Path)
    if ((Test-Path -LiteralPath $Path) -and (($env:Path -split ";") -notcontains $Path)) {
        $env:Path = "$Path;$env:Path"
    }
}

function Refresh-SessionPath {
    Add-PathIfExists "$env:ProgramFiles\nodejs"
    Add-PathIfExists "$env:USERPROFILE\.local\bin"
    Add-PathIfExists "$env:LOCALAPPDATA\Programs\Python\Python312"
    Add-PathIfExists "$env:LOCALAPPDATA\Programs\Python\Python312\Scripts"
    Add-PathIfExists "$env:ProgramFiles\Python312"
    Add-PathIfExists "$env:ProgramFiles\Python312\Scripts"

    try {
        $machinePath = [Environment]::GetEnvironmentVariable("Path", "Machine")
        $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
        $env:Path = "$machinePath;$userPath;$env:Path"
    } catch {
        Write-Warning "Could not refresh PATH from registry: $($_.Exception.Message)"
    }
}

function Invoke-Checked {
    param(
        [Parameter(Mandatory = $true)][string]$Title,
        [Parameter(Mandatory = $true)][scriptblock]$Action
    )

    Write-Host ""
    Write-Host "==> $Title"
    & $Action
}

function Install-WingetPackage {
    param(
        [Parameter(Mandatory = $true)][string]$Id,
        [Parameter(Mandatory = $true)][string]$Name
    )

    $winget = Get-CommandPath "winget.exe"
    if (-not $winget) {
        throw "winget is not available. Install App Installer from Microsoft Store, then run this script again."
    }

    Write-Host "Installing or updating $Name with winget..."
    & $winget install `
        --exact `
        --id $Id `
        --accept-package-agreements `
        --accept-source-agreements `
        --silent

    if ($LASTEXITCODE -ne 0) {
        throw "winget failed while installing $Name. Package id: $Id"
    }
}

function Ensure-Python {
    $py = Get-CommandPath "py.exe"
    if ($py) {
        & $py -3.12 --version *> $null
        if ($LASTEXITCODE -eq 0) {
            Write-Host "Python 3.12 is already available."
            return
        }
    }

    Install-WingetPackage -Id "Python.Python.3.12" -Name "Python 3.12"
    Refresh-SessionPath

    $py = Get-CommandPath "py.exe"
    if (-not $py) {
        throw "Python launcher py.exe was not found after installation."
    }
    & $py -3.12 --version
}

function Ensure-Node {
    if ((Get-CommandPath "node.exe") -and (Get-CommandPath "npm.cmd")) {
        Write-Host "Node.js and npm are already available."
        node --version
        npm --version
        return
    }

    Install-WingetPackage -Id "OpenJS.NodeJS.LTS" -Name "Node.js LTS"
    Refresh-SessionPath

    if (-not (Get-CommandPath "node.exe")) {
        throw "node.exe was not found after installation."
    }
    if (-not (Get-CommandPath "npm.cmd")) {
        throw "npm.cmd was not found after installation."
    }
    node --version
    npm --version
}

function Ensure-Uv {
    Refresh-SessionPath
    if (Get-CommandPath "uv.exe") {
        Write-Host "uv is already available."
        uv --version
        return
    }

    $wingetSucceeded = $false
    try {
        Install-WingetPackage -Id "astral-sh.uv" -Name "uv"
        $wingetSucceeded = $true
    } catch {
        Write-Warning "winget uv install failed: $($_.Exception.Message)"
    }

    Refresh-SessionPath
    if (-not (Get-CommandPath "uv.exe")) {
        Write-Host "Installing uv with the official installer..."
        $installer = "$env:TEMP\install-uv.ps1"
        Invoke-WebRequest -Uri "https://astral.sh/uv/install.ps1" -OutFile $installer
        powershell.exe -NoProfile -ExecutionPolicy Bypass -File $installer
        Refresh-SessionPath
    }

    if (-not (Get-CommandPath "uv.exe")) {
        $message = if ($wingetSucceeded) {
            "uv was installed but is not on PATH yet. Open a new terminal and run this script again."
        } else {
            "uv installation failed. Check your network or install uv manually."
        }
        throw $message
    }

    uv --version
}

function Ensure-VcRuntime {
    try {
        Install-WingetPackage -Id "Microsoft.VCRedist.2015+.x64" -Name "Microsoft Visual C++ Redistributable x64"
    } catch {
        Write-Warning "Visual C++ Runtime install/check failed: $($_.Exception.Message)"
        Write-Warning "If media or model runtimes fail later, install Microsoft Visual C++ Redistributable x64 manually."
    }
}

function Get-NvidiaGpuInfo {
    Refresh-SessionPath
    $nvidiaSmi = Get-CommandPath "nvidia-smi.exe"
    if (-not $nvidiaSmi) {
        return [pscustomobject]@{
            Available = $false
            Names = @()
            DriverVersion = $null
            CudaVersion = $null
        }
    }

    $names = @()
    $driverVersion = $null
    try {
        $queryOutput = & $nvidiaSmi --query-gpu=name,driver_version --format=csv,noheader 2>$null
        foreach ($line in $queryOutput) {
            $parts = @($line -split "," | ForEach-Object { $_.Trim() })
            if ($parts.Count -ge 1 -and $parts[0]) {
                $names += $parts[0]
            }
            if (-not $driverVersion -and $parts.Count -ge 2 -and $parts[1]) {
                $driverVersion = $parts[1]
            }
        }
    } catch {
        Write-Warning "Could not query NVIDIA GPU names: $($_.Exception.Message)"
    }

    $cudaVersion = $null
    try {
        $summary = (& $nvidiaSmi 2>$null) -join "`n"
        if ($summary -match "CUDA Version:\s*([0-9]+(?:\.[0-9]+)?)") {
            $cudaVersion = [version]$Matches[1]
        }
    } catch {
        Write-Warning "Could not query NVIDIA CUDA driver version: $($_.Exception.Message)"
    }

    return [pscustomobject]@{
        Available = $true
        Names = $names
        DriverVersion = $driverVersion
        CudaVersion = $cudaVersion
    }
}

function Resolve-PytorchWheelIndex {
    param([Parameter(Mandatory = $true)]$GpuInfo)

    if (-not $GpuInfo.Available) {
        return [pscustomobject]@{
            Name = "cpu"
            Url = "https://download.pytorch.org/whl/cpu"
            MirrorUrl = $null
            RequiredArch = $null
            Reason = "No NVIDIA GPU was detected."
        }
    }

    $gpuNames = ($GpuInfo.Names -join " ")
    $isRtx50Series = $gpuNames -match "(RTX\s*50|RTX\s*PRO.*50|5060|5070|5080|5090|Blackwell)"
    $cuda = $GpuInfo.CudaVersion

    if ($isRtx50Series) {
        if (-not $cuda -or $cuda -lt [version]"12.8") {
            $cudaText = if ($cuda) { $cuda.ToString() } else { "unknown" }
            throw "Detected $gpuNames, but the NVIDIA driver reports CUDA $cudaText. RTX 50-series GPUs require a PyTorch build with CUDA 12.8+ support. Update the NVIDIA driver, then run this script again."
        }
        return [pscustomobject]@{
            Name = "cu128"
            Url = "https://download.pytorch.org/whl/cu128"
            MirrorUrl = "https://mirrors.aliyun.com/pytorch-wheels/cu128"
            RequiredArch = "sm_120"
            Reason = "RTX 50-series GPU requires sm_120 support."
        }
    }

    if ($cuda -and $cuda -ge [version]"12.8") {
        return [pscustomobject]@{
            Name = "cu128"
            Url = "https://download.pytorch.org/whl/cu128"
            MirrorUrl = "https://mirrors.aliyun.com/pytorch-wheels/cu128"
            RequiredArch = $null
            Reason = "NVIDIA driver supports CUDA 12.8 or newer."
        }
    }

    if ($cuda -and $cuda -ge [version]"12.6") {
        return [pscustomobject]@{
            Name = "cu126"
            Url = "https://download.pytorch.org/whl/cu126"
            MirrorUrl = "https://mirrors.aliyun.com/pytorch-wheels/cu126"
            RequiredArch = $null
            Reason = "NVIDIA driver supports CUDA 12.6."
        }
    }

    return [pscustomobject]@{
        Name = "cu118"
        Url = "https://download.pytorch.org/whl/cu118"
        MirrorUrl = "https://mirrors.aliyun.com/pytorch-wheels/cu118"
        RequiredArch = $null
        Reason = "Using the broad CUDA 11.8 compatible PyTorch wheel."
    }
}

function Get-PytorchInstallIndexes {
    param([Parameter(Mandatory = $true)]$WheelIndex)

    $indexes = @()
    if ($env:SANTISZR_PYTORCH_INDEX_URL) {
        $indexes += [pscustomobject]@{
            Name = "custom"
            Url = $env:SANTISZR_PYTORCH_INDEX_URL
        }
    } elseif ($WheelIndex.MirrorUrl) {
        $indexes += [pscustomobject]@{
            Name = "aliyun"
            Url = $WheelIndex.MirrorUrl
        }
    }

    if ($WheelIndex.Url -and ($indexes.Url -notcontains $WheelIndex.Url)) {
        $indexes += [pscustomobject]@{
            Name = "official"
            Url = $WheelIndex.Url
        }
    }

    return $indexes
}

function Get-PypiInstallIndexes {
    $indexes = @()
    if ($env:SANTISZR_PYPI_INDEX_URL) {
        $indexes += [pscustomobject]@{
            Name = "custom"
            Url = $env:SANTISZR_PYPI_INDEX_URL
        }
    } else {
        $indexes += [pscustomobject]@{
            Name = "aliyun"
            Url = "https://mirrors.aliyun.com/pypi/simple"
        }
    }

    $indexes += [pscustomobject]@{
        Name = "official"
        Url = "https://pypi.org/simple"
    }

    return $indexes
}

function Get-HelperTorchInfo {
    param([Parameter(Mandatory = $true)][string]$PythonExe)

    if (-not (Test-Path -LiteralPath $PythonExe)) {
        return $null
    }

    $probe = @"
import json
try:
    import torch
    cuda_version = getattr(torch.version, 'cuda', None)
    try:
        arch = torch.cuda.get_arch_list()
    except Exception:
        arch = []
    print(json.dumps({'ok': True, 'version': torch.__version__, 'cuda': cuda_version, 'arch': arch}))
except Exception as exc:
    print(json.dumps({'ok': False, 'error': str(exc)}))
"@

    $output = & $PythonExe -c $probe 2>$null
    if ($LASTEXITCODE -ne 0 -or -not $output) {
        return [pscustomobject]@{
            Ok = $false
            Version = $null
            Cuda = $null
            Arch = @()
            Error = "Could not run torch probe."
        }
    }

    try {
        $parsed = ($output | Select-Object -Last 1) | ConvertFrom-Json
        return [pscustomobject]@{
            Ok = [bool]$parsed.ok
            Version = $parsed.version
            Cuda = $parsed.cuda
            Arch = @($parsed.arch)
            Error = $parsed.error
        }
    } catch {
        return [pscustomobject]@{
            Ok = $false
            Version = $null
            Cuda = $null
            Arch = @()
            Error = "Could not parse torch probe output: $output"
        }
    }
}

function Get-HelperNumpyInfo {
    param([Parameter(Mandatory = $true)][string]$PythonExe)

    if (-not (Test-Path -LiteralPath $PythonExe)) {
        return $null
    }

    $probe = @"
import json
try:
    import importlib.metadata as metadata
    import numpy
    module_version = getattr(numpy, '__version__', None)
    try:
        metadata_version = metadata.version('numpy')
    except Exception as exc:
        metadata_version = None
        metadata_error = str(exc)
    else:
        metadata_error = None
    print(json.dumps({'ok': bool(module_version and metadata_version), 'module_version': module_version, 'metadata_version': metadata_version, 'metadata_error': metadata_error}))
except Exception as exc:
    print(json.dumps({'ok': False, 'error': str(exc)}))
"@

    $output = & $PythonExe -c $probe 2>$null
    if ($LASTEXITCODE -ne 0 -or -not $output) {
        return [pscustomobject]@{
            Ok = $false
            ModuleVersion = $null
            MetadataVersion = $null
            Error = "Could not run numpy probe."
        }
    }

    try {
        $parsed = ($output | Select-Object -Last 1) | ConvertFrom-Json
        return [pscustomobject]@{
            Ok = [bool]$parsed.ok
            ModuleVersion = $parsed.module_version
            MetadataVersion = $parsed.metadata_version
            Error = if ($parsed.error) { $parsed.error } else { $parsed.metadata_error }
        }
    } catch {
        return [pscustomobject]@{
            Ok = $false
            ModuleVersion = $null
            MetadataVersion = $null
            Error = "Could not parse numpy probe output: $output"
        }
    }
}

function Repair-BrokenHelperNumpyFiles {
    param(
        [Parameter(Mandatory = $true)][string]$HelperName,
        [Parameter(Mandatory = $true)][string]$PythonExe
    )

    $pythonRoot = Split-Path -Parent $PythonExe
    $sitePackages = Join-Path $pythonRoot "Lib\site-packages"
    if (-not (Test-Path -LiteralPath $sitePackages)) {
        throw "$HelperName helper site-packages directory was not found: $sitePackages"
    }

    $resolvedSitePackages = (Resolve-Path -LiteralPath $sitePackages).Path
    $targets = @()
    foreach ($pattern in @("numpy", "numpy.libs", "numpy-*.dist-info")) {
        $targets += @(Get-ChildItem -LiteralPath $resolvedSitePackages -Force -Filter $pattern -ErrorAction SilentlyContinue)
    }

    foreach ($target in $targets) {
        $resolvedTarget = (Resolve-Path -LiteralPath $target.FullName).Path
        if (-not $resolvedTarget.StartsWith($resolvedSitePackages, [StringComparison]::OrdinalIgnoreCase)) {
            throw "Refusing to remove unexpected numpy path outside site-packages: $resolvedTarget"
        }
        Write-Host "Removing broken $HelperName helper numpy path: $resolvedTarget"
        Remove-Item -LiteralPath $resolvedTarget -Recurse -Force
    }
}

function Ensure-HelperNumpyRuntime {
    param(
        [Parameter(Mandatory = $true)][string]$HelperName,
        [Parameter(Mandatory = $true)][string]$PythonExe
    )

    $numpyInfo = Get-HelperNumpyInfo -PythonExe $PythonExe
    if ($numpyInfo -and $numpyInfo.Ok -and $numpyInfo.ModuleVersion -eq $numpyInfo.MetadataVersion) {
        Write-Host "$HelperName helper numpy is healthy: $($numpyInfo.ModuleVersion)."
        return
    }

    if ($numpyInfo) {
        Write-Host "$HelperName helper numpy needs repair: module=$($numpyInfo.ModuleVersion), metadata=$($numpyInfo.MetadataVersion), error=$($numpyInfo.Error)."
    } else {
        Write-Host "$HelperName helper numpy needs repair: numpy probe did not run."
    }

    $installed = $false
    $installErrors = @()
    foreach ($index in (Get-PypiInstallIndexes)) {
        Write-Host "Installing $HelperName helper numpy from $($index.Name) index: $($index.Url)..."
        & $PythonExe -m pip install --upgrade --force-reinstall --no-cache-dir "numpy==1.26.4" --index-url $index.Url
        if ($LASTEXITCODE -eq 0) {
            $installed = $true
            break
        }
        $installErrors += "$($index.Name): exit code $LASTEXITCODE"
        Write-Warning "numpy install from $($index.Name) failed. Trying the next index if available."
    }

    if (-not $installed) {
        Write-Warning "$HelperName helper numpy could not be repaired by pip because the installed package metadata may be broken. Removing local numpy package files and retrying."
        Repair-BrokenHelperNumpyFiles -HelperName $HelperName -PythonExe $PythonExe

        $installErrors = @()
        foreach ($index in (Get-PypiInstallIndexes)) {
            Write-Host "Installing clean $HelperName helper numpy from $($index.Name) index: $($index.Url)..."
            & $PythonExe -m pip install --no-cache-dir --no-deps "numpy==1.26.4" --index-url $index.Url
            if ($LASTEXITCODE -eq 0) {
                $installed = $true
                break
            }
            $installErrors += "$($index.Name): exit code $LASTEXITCODE"
            Write-Warning "clean numpy install from $($index.Name) failed. Trying the next index if available."
        }
    }

    if (-not $installed) {
        throw "Failed to repair numpy for $HelperName helper Python: $PythonExe. Attempts: $($installErrors -join '; ')"
    }

    $repairedInfo = Get-HelperNumpyInfo -PythonExe $PythonExe
    if (-not $repairedInfo -or -not $repairedInfo.Ok -or $repairedInfo.ModuleVersion -ne $repairedInfo.MetadataVersion) {
        throw "$HelperName helper numpy repair completed, but verification failed. module=$($repairedInfo.ModuleVersion), metadata=$($repairedInfo.MetadataVersion), error=$($repairedInfo.Error)"
    }
    Write-Host "$HelperName helper numpy verified: $($repairedInfo.ModuleVersion)."
}

function Test-HelperTorchMatches {
    param(
        [Parameter(Mandatory = $true)]$TorchInfo,
        [Parameter(Mandatory = $true)]$WheelIndex
    )

    if (-not $TorchInfo -or -not $TorchInfo.Ok) {
        return $false
    }

    if ($WheelIndex.Name -eq "cpu") {
        return -not $TorchInfo.Cuda
    }

    if (-not $TorchInfo.Cuda) {
        return $false
    }

    if ($WheelIndex.Name -eq "cu128" -and $TorchInfo.Cuda -notlike "12.8*") {
        return $false
    }
    if ($WheelIndex.Name -eq "cu126" -and $TorchInfo.Cuda -notlike "12.6*") {
        return $false
    }
    if ($WheelIndex.Name -eq "cu118" -and $TorchInfo.Cuda -notlike "11.8*") {
        return $false
    }

    if ($WheelIndex.RequiredArch -and ($TorchInfo.Arch -notcontains $WheelIndex.RequiredArch)) {
        return $false
    }

    return $true
}

function Ensure-HelperPytorchRuntime {
    param(
        [Parameter(Mandatory = $true)][string]$ProjectRoot,
        [Parameter(Mandatory = $true)]$WheelIndex
    )

    if ($env:SANTISZR_SKIP_HELPER_TORCH_INSTALL -eq "1") {
        Write-Warning "Skipping helper PyTorch install because SANTISZR_SKIP_HELPER_TORCH_INSTALL=1."
        return
    }

    $voxcpmPython = Join-Path $ProjectRoot "tools\voxcpm_python\python.exe"
    $tuilionnxPython = Join-Path $ProjectRoot "tools\tuilionnx_python\python.exe"
    $cosyvoicePython = Join-Path $ProjectRoot "tools\cosyvoice_python\python.exe"
    $missingRuntimeMessages = @()

    if (-not (Test-Path -LiteralPath $voxcpmPython)) {
        $missingRuntimeMessages += "VoxCPM helper runtime is missing: tools\voxcpm_python\python.exe"
    }
    if (
        -not (Test-Path -LiteralPath $tuilionnxPython) `
        -and -not (Test-Path -LiteralPath $cosyvoicePython)
    ) {
        $missingRuntimeMessages += "Avatar helper runtime is missing: provide tools\tuilionnx_python\python.exe or tools\cosyvoice_python\python.exe"
    }

    if ($missingRuntimeMessages.Count -gt 0) {
        $message = @(
            "Bundled helper runtimes are missing. The system prerequisites script installs Python/Node/uv and project dependencies, but it cannot recreate the large prebuilt model helper runtimes from pip alone.",
            "",
            "Copy these folders from a working SantiSZR machine into $ProjectRoot\tools:",
            "  - tools\voxcpm_python",
            "  - tools\tuilionnx_python or tools\cosyvoice_python",
            "",
            "Then run install-windows-prereqs.bat again so this script can update PyTorch for the current GPU.",
            "",
            "Missing:",
            (($missingRuntimeMessages | ForEach-Object { "  - $_" }) -join "`n")
        ) -join "`n"
        throw $message
    }

    $helperPythons = @(
        [pscustomobject]@{ Name = "VoxCPM"; Path = $voxcpmPython },
        [pscustomobject]@{ Name = "TuiliONNX/CosyVoice"; Path = $tuilionnxPython },
        [pscustomobject]@{ Name = "TuiliONNX/CosyVoice"; Path = $cosyvoicePython }
    )

    $seen = @{}
    foreach ($helper in $helperPythons) {
        if ($seen.ContainsKey($helper.Path)) {
            continue
        }
        $seen[$helper.Path] = $true

        if (-not (Test-Path -LiteralPath $helper.Path)) {
            Write-Warning "$($helper.Name) helper Python was not found: $($helper.Path). Copy the bundled tools folder or configure the matching SANTISZR_*_PYTHON variable."
            continue
        }

        $torchInfo = Get-HelperTorchInfo -PythonExe $helper.Path
        if (Test-HelperTorchMatches -TorchInfo $torchInfo -WheelIndex $WheelIndex) {
            Write-Host "$($helper.Name) helper PyTorch is already compatible: torch $($torchInfo.Version), CUDA $($torchInfo.Cuda)."
            Ensure-HelperNumpyRuntime -HelperName $helper.Name -PythonExe $helper.Path
            continue
        }

        if ($torchInfo -and $torchInfo.Ok) {
            Write-Host "$($helper.Name) helper PyTorch needs update: torch $($torchInfo.Version), CUDA $($torchInfo.Cuda), arch=$($torchInfo.Arch -join ',')."
        } elseif ($torchInfo) {
            Write-Host "$($helper.Name) helper PyTorch is missing or unreadable: $($torchInfo.Error)"
        }

        $installed = $false
        $installErrors = @()
        foreach ($index in (Get-PytorchInstallIndexes -WheelIndex $WheelIndex)) {
            Write-Host "Installing $($helper.Name) helper PyTorch from $($index.Name) index: $($index.Url) ($($WheelIndex.Reason))..."
            & $helper.Path -m pip install --upgrade --force-reinstall torch torchvision torchaudio --index-url $index.Url
            if ($LASTEXITCODE -eq 0) {
                $installed = $true
                break
            }
            $installErrors += "$($index.Name): exit code $LASTEXITCODE"
            Write-Warning "PyTorch install from $($index.Name) failed. Trying the next index if available."
        }

        if (-not $installed) {
            throw "Failed to install PyTorch for $($helper.Name) helper Python: $($helper.Path). Attempts: $($installErrors -join '; ')"
        }

        $updatedInfo = Get-HelperTorchInfo -PythonExe $helper.Path
        if (-not (Test-HelperTorchMatches -TorchInfo $updatedInfo -WheelIndex $WheelIndex)) {
            throw "$($helper.Name) helper PyTorch install completed, but compatibility verification failed. torch=$($updatedInfo.Version), cuda=$($updatedInfo.Cuda), arch=$($updatedInfo.Arch -join ',')"
        }
        Write-Host "$($helper.Name) helper PyTorch verified: torch $($updatedInfo.Version), CUDA $($updatedInfo.Cuda), arch=$($updatedInfo.Arch -join ',')."
        Ensure-HelperNumpyRuntime -HelperName $helper.Name -PythonExe $helper.Path
    }
}

function Test-GpuDriver {
    Write-Host ""
    Write-Host "==> Checking NVIDIA driver"
    Refresh-SessionPath
    $nvidiaSmi = Get-CommandPath "nvidia-smi.exe"
    if (-not $nvidiaSmi) {
        Write-Warning "nvidia-smi was not found. Install the NVIDIA driver manually before running GPU generation."
        return
    }
    & $nvidiaSmi
}

function Invoke-ProjectSetup {
    param(
        [Parameter(Mandatory = $true)][string]$ProjectRoot,
        [Parameter(Mandatory = $true)][string]$FrontendDir
    )

    Invoke-Checked "Installing Python dependencies with uv sync" {
        Push-Location $ProjectRoot
        try {
            uv sync
        } finally {
            Pop-Location
        }
    }

    Invoke-Checked "Installing frontend dependencies with npm install" {
        Push-Location $FrontendDir
        try {
            npm install
        } finally {
            Pop-Location
        }
    }

    if (-not $SkipFrontendBuild) {
        Invoke-Checked "Building frontend" {
            Push-Location $FrontendDir
            try {
                npm run build
            } finally {
                Pop-Location
            }
        }
    }

    if (-not $SkipPlaywright) {
        $venvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
        if (-not (Test-Path -LiteralPath $venvPython)) {
            throw "Project venv Python not found: $venvPython"
        }
        Invoke-Checked "Installing Playwright Chromium" {
            & $venvPython -m playwright install chromium --no-shell
        }
    }
}

Invoke-SelfElevate

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Resolve-Path (Join-Path $ScriptDir "..")
$FrontendDir = Join-Path $ProjectRoot "web"
$LogDir = Join-Path $ProjectRoot "data\logs"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

$LogFile = Join-Path $LogDir "install-windows-prereqs.log"
try {
    Start-Transcript -Path $LogFile -Append | Out-Null
} catch {
    Write-Warning "Could not start transcript: $($_.Exception.Message)"
}

try {
    Write-Host "Project root: $ProjectRoot"
    Write-Host "Log file: $LogFile"

    Invoke-Checked "Checking winget" {
        if (-not (Get-CommandPath "winget.exe")) {
            throw "winget is not available. Install App Installer from Microsoft Store first."
        }
        winget --version
    }

    Invoke-Checked "Installing Python 3.12" { Ensure-Python }
    Invoke-Checked "Installing Node.js LTS" { Ensure-Node }
    Invoke-Checked "Installing uv" { Ensure-Uv }
    Invoke-Checked "Installing Visual C++ Runtime" { Ensure-VcRuntime }
    Test-GpuDriver
    $GpuInfo = Get-NvidiaGpuInfo
    $PytorchWheelIndex = Resolve-PytorchWheelIndex -GpuInfo $GpuInfo
    Write-Host "Selected PyTorch wheel index: $($PytorchWheelIndex.Name) - $($PytorchWheelIndex.Url)"
    if ($PytorchWheelIndex.MirrorUrl) {
        Write-Host "Preferred PyTorch mirror: $($PytorchWheelIndex.MirrorUrl)"
    }

    if (-not $SkipProjectSetup) {
        Invoke-ProjectSetup -ProjectRoot $ProjectRoot -FrontendDir $FrontendDir
        Invoke-Checked "Checking helper PyTorch GPU compatibility" {
            Ensure-HelperPytorchRuntime -ProjectRoot $ProjectRoot -WheelIndex $PytorchWheelIndex
        }
    }

    Write-Host ""
    Write-Host "Install complete."
    Write-Host "Start the app with:"
    Write-Host "  $ProjectRoot\restart-web.bat"
    Write-Host ""
    Write-Host "Then open:"
    Write-Host "  http://127.0.0.1:5173"
} finally {
    try {
        Stop-Transcript | Out-Null
    } catch {
    }
}
