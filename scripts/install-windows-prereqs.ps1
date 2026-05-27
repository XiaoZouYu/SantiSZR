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

function Invoke-PythonProbe {
    param(
        [Parameter(Mandatory = $true)][string]$PythonExe,
        [Parameter(Mandatory = $true)][string]$Probe
    )

    $script:LastPythonProbeExitCode = -1
    $probePath = Join-Path ([System.IO.Path]::GetTempPath()) ("santiszr-probe-" + [guid]::NewGuid().ToString("N") + ".py")
    try {
        Set-Content -LiteralPath $probePath -Value $Probe -Encoding UTF8
        $startInfo = New-Object System.Diagnostics.ProcessStartInfo
        $startInfo.FileName = $PythonExe
        $startInfo.Arguments = "`"$probePath`""
        $startInfo.UseShellExecute = $false
        $startInfo.RedirectStandardOutput = $true
        $startInfo.RedirectStandardError = $true
        $startInfo.CreateNoWindow = $true

        $process = New-Object System.Diagnostics.Process
        $process.StartInfo = $startInfo
        [void]$process.Start()
        $stdout = $process.StandardOutput.ReadToEnd()
        [void]$process.StandardError.ReadToEnd()
        $process.WaitForExit()
        $script:LastPythonProbeExitCode = $process.ExitCode
        $global:LASTEXITCODE = $process.ExitCode
        if (-not $stdout) {
            return @()
        }
        return @($stdout -split "\r?\n" | Where-Object { $_ -ne "" })
    } finally {
        if (Test-Path -LiteralPath $probePath) {
            Remove-Item -LiteralPath $probePath -Force -ErrorAction SilentlyContinue
        }
    }
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

function Get-WebView2RuntimeVersion {
    $clientId = "{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"
    $registryPaths = @(
        "HKLM:\SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\$clientId",
        "HKLM:\SOFTWARE\Microsoft\EdgeUpdate\Clients\$clientId",
        "HKCU:\SOFTWARE\Microsoft\EdgeUpdate\Clients\$clientId"
    )

    foreach ($path in $registryPaths) {
        try {
            $item = Get-ItemProperty -Path $path -ErrorAction Stop
            if ($item.pv) {
                return [string]$item.pv
            }
        } catch {
        }
    }

    return $null
}

function Ensure-WebView2Runtime {
    param([Parameter(Mandatory = $true)][string]$ProjectRoot)

    $version = Get-WebView2RuntimeVersion
    if ($version) {
        Write-Host "Microsoft Edge WebView2 Runtime is already available: $version"
        return
    }

    try {
        Install-WingetPackage -Id "Microsoft.EdgeWebView2Runtime" -Name "Microsoft Edge WebView2 Runtime"
    } catch {
        Write-Warning "winget WebView2 Runtime install failed: $($_.Exception.Message)"
    }

    $version = Get-WebView2RuntimeVersion
    if ($version) {
        Write-Host "Microsoft Edge WebView2 Runtime verified: $version"
        return
    }

    Write-Host "Installing Microsoft Edge WebView2 Runtime with the official Evergreen Bootstrapper..."
    $installerDir = Join-Path $ProjectRoot "tools\installers"
    New-Item -ItemType Directory -Force -Path $installerDir | Out-Null
    $installer = Join-Path $installerDir "MicrosoftEdgeWebview2Setup.exe"
    Invoke-WebRequest -Uri "https://go.microsoft.com/fwlink/p/?LinkId=2124703" -OutFile $installer

    $process = Start-Process -FilePath $installer -ArgumentList @("/silent", "/install") -Wait -PassThru
    if ($process.ExitCode -ne 0) {
        throw "Microsoft Edge WebView2 Runtime installer failed with exit code $($process.ExitCode). Installer: $installer"
    }

    $version = Get-WebView2RuntimeVersion
    if (-not $version) {
        throw "Microsoft Edge WebView2 Runtime installation completed, but verification failed."
    }

    Write-Host "Microsoft Edge WebView2 Runtime verified: $version"
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

function Test-WhisperModelDir {
    param([Parameter(Mandatory = $true)][string]$Path)

    if (-not (Test-Path -LiteralPath $Path -PathType Container)) {
        return $false
    }

    foreach ($name in @("config.json", "model.bin", "tokenizer.json")) {
        if (-not (Test-Path -LiteralPath (Join-Path $Path $name) -PathType Leaf)) {
            return $false
        }
    }

    $vocabulary = @(Get-ChildItem -LiteralPath $Path -Filter "vocabulary.*" -File -ErrorAction SilentlyContinue)
    return $vocabulary.Count -gt 0
}

function Test-WhisperModelFiles {
    param(
        [Parameter(Mandatory = $true)][string]$ModelRoot,
        [Parameter(Mandatory = $true)][string]$ModelName
    )

    $directDir = Join-Path $ModelRoot $ModelName
    if (Test-WhisperModelDir -Path $directDir) {
        return $true
    }

    $cacheDir = Join-Path $ModelRoot "models--Systran--faster-whisper-$ModelName"
    $refPath = Join-Path $cacheDir "refs\main"
    if (Test-Path -LiteralPath $refPath -PathType Leaf) {
        try {
            $snapshotName = (Get-Content -LiteralPath $refPath -Raw).Trim()
            if ($snapshotName) {
                $snapshotDir = Join-Path (Join-Path $cacheDir "snapshots") $snapshotName
                if (Test-WhisperModelDir -Path $snapshotDir) {
                    return $true
                }
            }
        } catch {
        }
    }

    $snapshotsRoot = Join-Path $cacheDir "snapshots"
    if (Test-Path -LiteralPath $snapshotsRoot -PathType Container) {
        $snapshots = @(Get-ChildItem -LiteralPath $snapshotsRoot -Directory -ErrorAction SilentlyContinue)
        foreach ($snapshot in $snapshots) {
            if (Test-WhisperModelDir -Path $snapshot.FullName) {
                return $true
            }
        }
    }

    return $false
}

function Get-HuggingFaceDownloadEndpoints {
    $endpoints = @()

    if ($env:SANTISZR_HF_ENDPOINT) {
        $endpoints += [pscustomobject]@{
            Name = "custom"
            Url = $env:SANTISZR_HF_ENDPOINT
        }
    } elseif ($env:HF_ENDPOINT) {
        $endpoints += [pscustomobject]@{
            Name = "existing"
            Url = $env:HF_ENDPOINT
        }
    } else {
        $endpoints += [pscustomobject]@{
            Name = "hf-mirror"
            Url = "https://hf-mirror.com"
        }
    }

    if ($endpoints.Url -notcontains "") {
        $endpoints += [pscustomobject]@{
            Name = "official"
            Url = ""
        }
    }

    return $endpoints
}

function Ensure-WhisperModel {
    param([Parameter(Mandatory = $true)][string]$ProjectRoot)

    $modelName = if ($env:SANTISZR_WHISPER_MODEL_NAME) { $env:SANTISZR_WHISPER_MODEL_NAME } else { "small" }
    $modelRoot = if ($env:SANTISZR_WHISPER_MODEL_DIR) { $env:SANTISZR_WHISPER_MODEL_DIR } else { Join-Path $ProjectRoot "models\whisper" }
    $pythonExe = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
    if (-not (Test-Path -LiteralPath $pythonExe)) {
        $pythonExe = "python"
    }

    New-Item -ItemType Directory -Force -Path $modelRoot | Out-Null

    if (Test-WhisperModelFiles -ModelRoot $modelRoot -ModelName $modelName) {
        Write-Host "Whisper model is already available: $modelRoot ($modelName)."
        return
    }

    $probe = @"
import json
import os
import pathlib

from faster_whisper.utils import download_model

model_name = os.environ.get("SANTISZR_WHISPER_MODEL_NAME", "small").strip() or "small"
model_root = pathlib.Path(os.environ["SANTISZR_WHISPER_MODEL_DIR"]).expanduser().resolve()
output_dir = model_root / model_name
model_root.mkdir(parents=True, exist_ok=True)
path = download_model(model_name, output_dir=str(output_dir), cache_dir=str(model_root))
print(json.dumps({"ok": True, "path": str(path)}))
"@

    $oldWhisperModelDir = $env:SANTISZR_WHISPER_MODEL_DIR
    $oldWhisperModelName = $env:SANTISZR_WHISPER_MODEL_NAME
    $oldHfEndpoint = $env:HF_ENDPOINT
    $downloadErrors = @()

    try {
        $env:SANTISZR_WHISPER_MODEL_DIR = $modelRoot
        $env:SANTISZR_WHISPER_MODEL_NAME = $modelName

        foreach ($endpoint in (Get-HuggingFaceDownloadEndpoints)) {
            if ($endpoint.Url) {
                $env:HF_ENDPOINT = $endpoint.Url
                Write-Host "Downloading Whisper model $modelName from $($endpoint.Name): $($endpoint.Url)..."
            } else {
                Remove-Item Env:\HF_ENDPOINT -ErrorAction SilentlyContinue
                Write-Host "Downloading Whisper model $modelName from official Hugging Face..."
            }

            $output = Invoke-PythonProbe -PythonExe $pythonExe -Probe $probe
            if ($script:LastPythonProbeExitCode -eq 0 -and (Test-WhisperModelFiles -ModelRoot $modelRoot -ModelName $modelName)) {
                $downloadPath = ""
                try {
                    $downloadPath = (($output | Select-Object -Last 1) | ConvertFrom-Json).path
                } catch {
                }
                if ($downloadPath) {
                    Write-Host "Whisper model verified: $downloadPath"
                } else {
                    Write-Host "Whisper model verified: $modelRoot ($modelName)."
                }
                return
            }

            $downloadErrors += "$($endpoint.Name): exit code $($script:LastPythonProbeExitCode)"
            Write-Warning "Whisper model download from $($endpoint.Name) failed or produced incomplete files. Trying the next endpoint if available."
        }
    } finally {
        if ($null -eq $oldWhisperModelDir) {
            Remove-Item Env:\SANTISZR_WHISPER_MODEL_DIR -ErrorAction SilentlyContinue
        } else {
            $env:SANTISZR_WHISPER_MODEL_DIR = $oldWhisperModelDir
        }
        if ($null -eq $oldWhisperModelName) {
            Remove-Item Env:\SANTISZR_WHISPER_MODEL_NAME -ErrorAction SilentlyContinue
        } else {
            $env:SANTISZR_WHISPER_MODEL_NAME = $oldWhisperModelName
        }
        if ($null -eq $oldHfEndpoint) {
            Remove-Item Env:\HF_ENDPOINT -ErrorAction SilentlyContinue
        } else {
            $env:HF_ENDPOINT = $oldHfEndpoint
        }
    }

    throw "Failed to download Whisper model $modelName into $modelRoot. Attempts: $($downloadErrors -join '; ')"
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

    $output = Invoke-PythonProbe -PythonExe $PythonExe -Probe $probe
    if ($script:LastPythonProbeExitCode -ne 0 -or -not $output) {
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

    $output = Invoke-PythonProbe -PythonExe $PythonExe -Probe $probe
    if ($script:LastPythonProbeExitCode -ne 0 -or -not $output) {
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

function ConvertTo-VersionOrZero {
    param([string]$VersionText)

    if (-not $VersionText) {
        return [version]"0.0.0"
    }
    $cleanVersion = ($VersionText -split "\+")[0]
    try {
        return [version]$cleanVersion
    } catch {
        return [version]"0.0.0"
    }
}

function Get-HelperOnnxRuntimeInfo {
    param([Parameter(Mandatory = $true)][string]$PythonExe)

    if (-not (Test-Path -LiteralPath $PythonExe)) {
        return $null
    }

    $probe = @"
import json
try:
    import onnxruntime as ort
    providers = ort.get_available_providers()
    print(json.dumps({'ok': True, 'version': ort.__version__, 'providers': providers}))
except Exception as exc:
    print(json.dumps({'ok': False, 'error': str(exc)}))
"@

    $output = Invoke-PythonProbe -PythonExe $PythonExe -Probe $probe
    if ($script:LastPythonProbeExitCode -ne 0 -or -not $output) {
        return [pscustomobject]@{
            Ok = $false
            Version = $null
            Providers = @()
            Error = "Could not run ONNX Runtime probe."
        }
    }

    try {
        $parsed = ($output | Select-Object -Last 1) | ConvertFrom-Json
        return [pscustomobject]@{
            Ok = [bool]$parsed.ok
            Version = $parsed.version
            Providers = @($parsed.providers)
            Error = $parsed.error
        }
    } catch {
        return [pscustomobject]@{
            Ok = $false
            Version = $null
            Providers = @()
            Error = "Could not parse ONNX Runtime probe output: $output"
        }
    }
}

function Test-HelperOnnxRuntimeMatches {
    param(
        [Parameter(Mandatory = $true)]$OrtInfo,
        [Parameter(Mandatory = $true)]$WheelIndex
    )

    if (-not $OrtInfo -or -not $OrtInfo.Ok) {
        return $false
    }
    if ($OrtInfo.Providers -notcontains "CUDAExecutionProvider") {
        return $false
    }

    $version = ConvertTo-VersionOrZero -VersionText $OrtInfo.Version
    if ($WheelIndex.Name -eq "cu128" -and $version -lt [version]"1.24.0") {
        return $false
    }
    if ($WheelIndex.Name -eq "cu126" -and $version -lt [version]"1.20.0") {
        return $false
    }
    if ($WheelIndex.Name -eq "cu118" -and $version -lt [version]"1.18.0") {
        return $false
    }
    return $true
}

function Resolve-OnnxRuntimeGpuRequirement {
    param([Parameter(Mandatory = $true)]$WheelIndex)

    if ($WheelIndex.Name -eq "cu128") {
        return "onnxruntime-gpu>=1.24.0"
    }
    if ($WheelIndex.Name -eq "cu126") {
        return "onnxruntime-gpu>=1.20.0"
    }
    return "onnxruntime-gpu>=1.18.0"
}

function Test-HelperOnnxRuntimeCudaSession {
    param(
        [Parameter(Mandatory = $true)][string]$ProjectRoot,
        [Parameter(Mandatory = $true)][string]$PythonExe
    )

    $probeModel = Join-Path $ProjectRoot "models\tuilionnx\checkpoints\auxiliary\models\buffalo_l\2d106det.onnx"
    $probeModelLiteral = $probeModel.Replace("\", "\\").Replace("'", "\'")
    $probe = @"
import json
import os
import pathlib
import sys

def add_dll_dir(path):
    if not path.exists():
        return
    text = str(path)
    try:
        os.add_dll_directory(text)
    except Exception:
        pass
    current_path = os.environ.get('PATH', '')
    if text not in current_path.split(os.pathsep):
        os.environ['PATH'] = text + os.pathsep + current_path

python_root = pathlib.Path(sys.executable).resolve().parent
site_packages = python_root / 'Lib' / 'site-packages'
for directory in (
    python_root,
    python_root / 'bin',
    python_root / 'DLLs',
    python_root / 'Library' / 'bin',
    site_packages / 'onnxruntime' / 'capi',
    site_packages / 'torch' / 'lib',
):
    add_dll_dir(directory)

try:
    import torch
    if torch.cuda.is_available():
        torch.cuda.init()
    import onnxruntime as ort
    if hasattr(ort, 'preload_dlls'):
        try:
            ort.preload_dlls()
        except TypeError:
            ort.preload_dlls(cuda=True, cudnn=True, msvc=True)

    providers = ort.get_available_providers()
    if 'CUDAExecutionProvider' not in providers:
        print(json.dumps({'ok': False, 'version': ort.__version__, 'providers': providers, 'error': 'CUDAExecutionProvider is not available.'}))
        raise SystemExit

    model_path = pathlib.Path('$probeModelLiteral')
    if not model_path.exists():
        print(json.dumps({'ok': True, 'version': ort.__version__, 'providers': providers, 'session_probed': False}))
        raise SystemExit

    options = ort.SessionOptions()
    options.intra_op_num_threads = 1
    options.inter_op_num_threads = 1
    session = ort.InferenceSession(str(model_path), sess_options=options, providers=['CUDAExecutionProvider'])
    active = session.get_providers()
    ok = bool(active) and active[0] == 'CUDAExecutionProvider'
    if ok:
        import numpy as np
        input_meta = session.get_inputs()[0]
        shape = [1 if not isinstance(dim, int) else dim for dim in input_meta.shape]
        session.run(None, {input_meta.name: np.zeros(tuple(shape), dtype=np.float32)})
    print(json.dumps({'ok': ok, 'version': ort.__version__, 'providers': providers, 'active_providers': active, 'session_probed': True}))
except Exception as exc:
    print(json.dumps({'ok': False, 'error': str(exc)}))
"@

    $output = Invoke-PythonProbe -PythonExe $PythonExe -Probe $probe
    if ($script:LastPythonProbeExitCode -ne 0 -or -not $output) {
        return [pscustomobject]@{
            Ok = $false
            Version = $null
            Providers = @()
            ActiveProviders = @()
            SessionProbed = $false
            Error = "Could not run ONNX Runtime CUDA session probe."
        }
    }

    try {
        $parsed = ($output | Select-Object -Last 1) | ConvertFrom-Json
        return [pscustomobject]@{
            Ok = [bool]$parsed.ok
            Version = $parsed.version
            Providers = @($parsed.providers)
            ActiveProviders = @($parsed.active_providers)
            SessionProbed = [bool]$parsed.session_probed
            Error = $parsed.error
        }
    } catch {
        return [pscustomobject]@{
            Ok = $false
            Version = $null
            Providers = @()
            ActiveProviders = @()
            SessionProbed = $false
            Error = "Could not parse ONNX Runtime CUDA probe output: $output"
        }
    }
}

function Ensure-HelperOnnxRuntimeGpu {
    param(
        [Parameter(Mandatory = $true)][string]$ProjectRoot,
        [Parameter(Mandatory = $true)][string]$HelperName,
        [Parameter(Mandatory = $true)][string]$PythonExe,
        [Parameter(Mandatory = $true)]$WheelIndex
    )

    $ortInfo = Get-HelperOnnxRuntimeInfo -PythonExe $PythonExe
    $sessionInfo = Test-HelperOnnxRuntimeCudaSession -ProjectRoot $ProjectRoot -PythonExe $PythonExe
    if (
        (Test-HelperOnnxRuntimeMatches -OrtInfo $ortInfo -WheelIndex $WheelIndex) `
        -and $sessionInfo `
        -and $sessionInfo.Ok
    ) {
        $activeText = if ($sessionInfo.ActiveProviders.Count -gt 0) { $sessionInfo.ActiveProviders -join "," } else { $ortInfo.Providers -join "," }
        Write-Host "$HelperName helper ONNX Runtime GPU is ready: onnxruntime-gpu $($ortInfo.Version), providers=$activeText."
        return
    }

    if ($ortInfo -and $ortInfo.Ok) {
        Write-Host "$HelperName helper ONNX Runtime GPU needs update or repair: version=$($ortInfo.Version), providers=$($ortInfo.Providers -join ','), sessionError=$($sessionInfo.Error)."
    } elseif ($ortInfo) {
        Write-Host "$HelperName helper ONNX Runtime GPU is missing or unreadable: $($ortInfo.Error)"
    } else {
        Write-Host "$HelperName helper ONNX Runtime GPU is missing."
    }

    Stop-HelperPythonProcesses -HelperName $HelperName -PythonExe $PythonExe

    $requirement = Resolve-OnnxRuntimeGpuRequirement -WheelIndex $WheelIndex
    $installed = $false
    $installErrors = @()
    foreach ($index in (Get-PypiInstallIndexes)) {
        Write-Host "Installing $HelperName helper $requirement from $($index.Name) index: $($index.Url)..."
        & $PythonExe -m pip install --upgrade --force-reinstall --no-deps --no-cache-dir $requirement --index-url $index.Url
        if ($LASTEXITCODE -eq 0) {
            $installed = $true
            break
        }
        $installErrors += "$($index.Name): exit code $LASTEXITCODE"
        Write-Warning "ONNX Runtime GPU install from $($index.Name) failed. Trying the next index if available."
    }

    if (-not $installed) {
        throw "Failed to install ONNX Runtime GPU for $HelperName helper Python: $PythonExe. Attempts: $($installErrors -join '; ')"
    }

    $updatedInfo = Get-HelperOnnxRuntimeInfo -PythonExe $PythonExe
    $updatedSessionInfo = Test-HelperOnnxRuntimeCudaSession -ProjectRoot $ProjectRoot -PythonExe $PythonExe
    if (
        -not (Test-HelperOnnxRuntimeMatches -OrtInfo $updatedInfo -WheelIndex $WheelIndex) `
        -or -not $updatedSessionInfo `
        -or -not $updatedSessionInfo.Ok
    ) {
        throw "$HelperName helper ONNX Runtime GPU install completed, but CUDA session verification failed. version=$($updatedInfo.Version), providers=$($updatedInfo.Providers -join ','), error=$($updatedSessionInfo.Error)"
    }
    Write-Host "$HelperName helper ONNX Runtime GPU verified: onnxruntime-gpu $($updatedInfo.Version), providers=$($updatedSessionInfo.ActiveProviders -join ',')."
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

    Stop-HelperPythonProcesses -HelperName $HelperName -PythonExe $PythonExe

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
        try {
            Remove-Item -LiteralPath $resolvedTarget -Recurse -Force
        } catch {
            throw "Could not remove broken $HelperName helper numpy path because it is still locked: $resolvedTarget. Close running SantiSZR/backend/helper processes and run this script again. Original error: $($_.Exception.Message)"
        }
    }
}

function Stop-HelperPythonProcesses {
    param(
        [Parameter(Mandatory = $true)][string]$HelperName,
        [Parameter(Mandatory = $true)][string]$PythonExe
    )

    $pythonRoot = (Resolve-Path -LiteralPath (Split-Path -Parent $PythonExe)).Path
    $currentProcessId = $PID
    $processes = @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
        if (-not $_.ProcessId -or $_.ProcessId -eq $currentProcessId) {
            return $false
        }
        $exePath = if ($_.ExecutablePath) { [string]$_.ExecutablePath } else { "" }
        $commandLine = if ($_.CommandLine) { [string]$_.CommandLine } else { "" }
        return (
            $exePath.StartsWith($pythonRoot, [StringComparison]::OrdinalIgnoreCase) `
            -or $commandLine.IndexOf($pythonRoot, [StringComparison]::OrdinalIgnoreCase) -ge 0
        )
    })

    foreach ($process in $processes) {
        Write-Host "Stopping $HelperName helper process $($process.ProcessId): $($process.ExecutablePath)"
        try {
            Stop-Process -Id $process.ProcessId -Force -ErrorAction Stop
        } catch {
            Write-Warning "Could not stop $HelperName helper process $($process.ProcessId): $($_.Exception.Message)"
        }
    }

    if ($processes.Count -gt 0) {
        Start-Sleep -Seconds 2
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

function Ensure-HelperPytorchDependencies {
    param(
        [Parameter(Mandatory = $true)][string]$HelperName,
        [Parameter(Mandatory = $true)][string]$PythonExe
    )

    $packages = @(
        "filelock",
        "typing-extensions>=4.10.0",
        "setuptools<82",
        "sympy>=1.13.3",
        "networkx>=2.5.1",
        "jinja2",
        "fsspec>=0.8.5",
        "pillow>=5.3.0",
        "mpmath<1.4,>=1.1.0"
    )

    $installed = $false
    $installErrors = @()
    foreach ($index in (Get-PypiInstallIndexes)) {
        Write-Host "Installing $HelperName helper PyTorch dependencies from $($index.Name) index: $($index.Url)..."
        & $PythonExe -m pip install --upgrade --no-cache-dir @packages --index-url $index.Url
        if ($LASTEXITCODE -eq 0) {
            $installed = $true
            break
        }
        $installErrors += "$($index.Name): exit code $LASTEXITCODE"
        Write-Warning "PyTorch dependency install from $($index.Name) failed. Trying the next index if available."
    }

    if (-not $installed) {
        throw "Failed to install PyTorch dependencies for $HelperName helper Python: $PythonExe. Attempts: $($installErrors -join '; ')"
    }
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
        [pscustomobject]@{ Name = "VoxCPM"; Path = $voxcpmPython; RequiresOnnxRuntimeGpu = $false },
        [pscustomobject]@{ Name = "TuiliONNX/CosyVoice"; Path = $tuilionnxPython; RequiresOnnxRuntimeGpu = $true },
        [pscustomobject]@{ Name = "TuiliONNX/CosyVoice"; Path = $cosyvoicePython; RequiresOnnxRuntimeGpu = $true }
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
            if ($helper.RequiresOnnxRuntimeGpu) {
                Ensure-HelperOnnxRuntimeGpu -ProjectRoot $ProjectRoot -HelperName $helper.Name -PythonExe $helper.Path -WheelIndex $WheelIndex
            }
            continue
        }

        if ($torchInfo -and $torchInfo.Ok) {
            Write-Host "$($helper.Name) helper PyTorch needs update: torch $($torchInfo.Version), CUDA $($torchInfo.Cuda), arch=$($torchInfo.Arch -join ',')."
        } elseif ($torchInfo) {
            Write-Host "$($helper.Name) helper PyTorch is missing or unreadable: $($torchInfo.Error)"
        }

        Ensure-HelperNumpyRuntime -HelperName $helper.Name -PythonExe $helper.Path
        Ensure-HelperPytorchDependencies -HelperName $helper.Name -PythonExe $helper.Path

        $installed = $false
        $installErrors = @()
        foreach ($index in (Get-PytorchInstallIndexes -WheelIndex $WheelIndex)) {
            Write-Host "Installing $($helper.Name) helper PyTorch from $($index.Name) index: $($index.Url) ($($WheelIndex.Reason))..."
            & $helper.Path -m pip install --upgrade --force-reinstall --no-deps torch torchvision torchaudio --index-url $index.Url
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
        if ($helper.RequiresOnnxRuntimeGpu) {
            Ensure-HelperOnnxRuntimeGpu -ProjectRoot $ProjectRoot -HelperName $helper.Name -PythonExe $helper.Path -WheelIndex $WheelIndex
        }
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
    Invoke-Checked "Installing Microsoft Edge WebView2 Runtime" { Ensure-WebView2Runtime -ProjectRoot $ProjectRoot }
    Test-GpuDriver
    $GpuInfo = Get-NvidiaGpuInfo
    $PytorchWheelIndex = Resolve-PytorchWheelIndex -GpuInfo $GpuInfo
    Write-Host "Selected PyTorch wheel index: $($PytorchWheelIndex.Name) - $($PytorchWheelIndex.Url)"
    if ($PytorchWheelIndex.MirrorUrl) {
        Write-Host "Preferred PyTorch mirror: $($PytorchWheelIndex.MirrorUrl)"
    }

    if (-not $SkipProjectSetup) {
        Invoke-ProjectSetup -ProjectRoot $ProjectRoot -FrontendDir $FrontendDir
        Invoke-Checked "Downloading Whisper model for ultimate clone" {
            Ensure-WhisperModel -ProjectRoot $ProjectRoot
        }
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
