# Post-install: copy configs, optional port check, register Windows services (UTF-8 BOM).
param(
    [Parameter(Mandatory = $true)]
    [string]$AppDir,
    [Parameter(Mandatory = $true)]
    [string]$ConfigDir,
    [string]$InstallBridgeSvc = "0",
    [string]$InstallWebSvc = "0",
    [string]$InstallBridge = "1",
    [string]$InstallWeb = "0"
)

$ErrorActionPreference = "Stop"

function Test-IsAdministrator {
    $principal = New-Object Security.Principal.WindowsPrincipal(
        [Security.Principal.WindowsIdentity]::GetCurrent())
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Write-InstallLog {
    param([string]$Message)
    $logDir = Join-Path $ConfigDir "logs"
    New-Item -ItemType Directory -Force -Path $logDir | Out-Null
    $logPath = Join-Path $logDir "post-install.log"
    $line = "{0} {1}" -f (Get-Date -Format "o"), $Message
    Add-Content -Path $logPath -Value $line -Encoding UTF8
}

Write-InstallLog "post-install start AppDir=$AppDir InstallBridgeSvc=$InstallBridgeSvc InstallWebSvc=$InstallWebSvc InstallBridge=$InstallBridge InstallWeb=$InstallWeb"

New-Item -ItemType Directory -Force -Path $ConfigDir | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $ConfigDir "logs") | Out-Null

$bridgeExample = Join-Path $AppDir "config\cornerstone-bridge.config.example.json"
$webExample = Join-Path $AppDir "config\cornerstone-web.config.example.json"
$bridgeCfg = Join-Path $ConfigDir "cornerstone-bridge.config.json"
$webCfg = Join-Path $ConfigDir "cornerstone-web.config.json"
$queueJson = Join-Path $ConfigDir "cornerstone-bridge.add-samples-queue.json"

if ($InstallBridge -eq "1" -and (Test-Path $bridgeExample)) {
    if (-not (Test-Path $bridgeCfg)) {
        Copy-Item $bridgeExample $bridgeCfg -Force
    }
}
if ($InstallWeb -eq "1" -and (Test-Path $webExample)) {
    if (-not (Test-Path $webCfg)) {
        Copy-Item $webExample $webCfg -Force
    }
}

if ((Test-Path $bridgeCfg) -and -not (Test-Path $queueJson)) {
    $utf8NoBom = New-Object System.Text.UTF8Encoding $false
    [System.IO.File]::WriteAllText($queueJson, '{"version":1,"items":[]}', $utf8NoBom)
}

$validate = Join-Path $AppDir "scripts\validate-install.ps1"
if (Test-Path $validate) {
    try {
        & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $validate `
            -ConfigDir $ConfigDir `
            -InstallBridge $InstallBridge `
            -InstallWeb $InstallWeb `
            -NonInteractive
        Write-InstallLog "validate-install exit=$LASTEXITCODE"
    } catch {
        Write-InstallLog "validate-install warning: $_"
    }
} else {
    Write-InstallLog "validate-install.ps1 not found, skipped"
}

$doBridge = $InstallBridgeSvc -eq "1"
$doWeb = $InstallWebSvc -eq "1"
Write-InstallLog "service install doBridge=$doBridge doWeb=$doWeb"

if ($doBridge -or $doWeb) {
    $installer = Join-Path $AppDir "scripts\install-services.ps1"
    if (-not (Test-Path $installer)) {
        throw "install-services.ps1 not found: $installer"
    }

    $invokeParams = @{
        AppDir    = $AppDir
        ConfigDir = $ConfigDir
    }
    if ($doBridge) { $invokeParams.InstallBridge = $true }
    if ($doWeb) { $invokeParams.InstallWeb = $true }

    try {
        # 安装程序已 PrivilegesRequired=admin；勿再 RunAs（静默安装无法二次 UAC，会 exit -196608）
        if (Test-IsAdministrator) {
            Write-InstallLog "install-services: in-process (already elevated)"
            & $installer @invokeParams
            $svcExit = $LASTEXITCODE
        } else {
            Write-InstallLog "install-services: elevating via RunAs"
            $svcArgs = @(
                "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $installer,
                "-AppDir", $AppDir,
                "-ConfigDir", $ConfigDir
            )
            if ($doBridge) { $svcArgs += "-InstallBridge" }
            if ($doWeb) { $svcArgs += "-InstallWeb" }
            $proc = Start-Process -FilePath "powershell.exe" -ArgumentList $svcArgs -Verb RunAs -Wait -PassThru
            $svcExit = $proc.ExitCode
        }
        Write-InstallLog "install-services.ps1 exit=$svcExit"
        if ($svcExit -ne 0) {
            $detail = Get-Content (Join-Path $ConfigDir "logs\install-services.log") -ErrorAction SilentlyContinue | Select-Object -Last 15
            throw "install-services.ps1 failed (exit $svcExit). $(if ($detail) { $detail -join ' ; ' } else { 'see install-services.log' })"
        }
    } catch {
        Write-InstallLog "install-services.ps1 failed: $_"
        throw
    }

    foreach ($svcName in @(
            $(if ($doBridge) { "CornerstoneBridge" }),
            $(if ($doWeb) { "CornerstoneWeb" })
        )) {
        if (-not $svcName) { continue }
        $svc = Get-Service -Name $svcName -ErrorAction SilentlyContinue
        if (-not $svc) {
            throw "Service not registered: $svcName (search Cornerstone in services.msc)"
        }
        Write-InstallLog "service $svcName status=$($svc.Status)"
    }
} else {
    Write-InstallLog "service install skipped (InstallBridgeSvc/InstallWebSvc not 1)"
}

Write-InstallLog "post-install done"
