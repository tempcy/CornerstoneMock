# Register Cornerstone Bridge / Web as Windows services via NSSM (requires Administrator).
param(
    [Parameter(Mandatory = $true)]
    [string]$AppDir,
    [Parameter(Mandatory = $true)]
    [string]$ConfigDir,
    [switch]$InstallBridge,
    [switch]$InstallWeb
)

$ErrorActionPreference = "Stop"

function Test-IsAdministrator {
    $principal = New-Object Security.Principal.WindowsPrincipal(
        [Security.Principal.WindowsIdentity]::GetCurrent())
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Write-InstallSvcLog {
    param([string]$Message)
    $logDir = Join-Path $ConfigDir "logs"
    New-Item -ItemType Directory -Force -Path $logDir | Out-Null
    $line = "{0} {1}" -f (Get-Date -Format "o"), $Message
    Add-Content -Path (Join-Path $logDir "install-services.log") -Value $line -Encoding UTF8
}

if (-not (Test-IsAdministrator)) {
    Write-Host "[install] Administrator required; re-launching elevated ..."
    $elevateArgs = @(
        "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $PSCommandPath,
        "-AppDir", $AppDir,
        "-ConfigDir", $ConfigDir
    )
    if ($InstallBridge) { $elevateArgs += "-InstallBridge" }
    if ($InstallWeb) { $elevateArgs += "-InstallWeb" }
    $proc = Start-Process -FilePath "powershell.exe" -ArgumentList $elevateArgs -Verb RunAs -Wait -PassThru
    exit $proc.ExitCode
}

$nssm = Join-Path $AppDir "tools\nssm.exe"
if (-not (Test-Path $nssm)) {
    throw "NSSM not found: $nssm"
}

$logDir = Join-Path $ConfigDir "logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
Write-InstallSvcLog "start AppDir=$AppDir InstallBridge=$InstallBridge InstallWeb=$InstallWeb admin=True"

function Invoke-Nssm {
    param([Parameter(ValueFromRemainingArguments = $true)][string[]]$NssmArgs)
    $prevEap = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $output = & $nssm @NssmArgs 2>&1 | ForEach-Object { "$_" }
    } finally {
        $ErrorActionPreference = $prevEap
    }
    $code = $LASTEXITCODE
    $cmd = "nssm $($NssmArgs -join ' ')"
    Write-InstallSvcLog "$cmd -> exit=$code $(if ($output) { ($output -join ' | ') } else { '' })"
    if ($code -ne 0) {
        throw "NSSM failed (exit $code): $cmd ; $($output -join ' ')"
    }
}

function Ensure-Removed([string]$Name) {
    $existing = Get-Service -Name $Name -ErrorAction SilentlyContinue
    if (-not $existing) {
        Write-InstallSvcLog "Ensure-Removed $Name : not present"
        return
    }
    Write-InstallSvcLog "Ensure-Removed $Name : status=$($existing.Status)"
    if ($existing.Status -ne "Stopped") {
        Stop-Service -Name $Name -Force -ErrorAction SilentlyContinue
        Start-Sleep -Seconds 2
    }
    # 用 sc delete 卸载旧服务；勿用 nssm stop/remove（非提升时 OpenService 会拒绝访问）
    $scOut = & sc.exe delete $Name 2>&1 | ForEach-Object { "$_" }
    Write-InstallSvcLog "sc delete $Name -> $LASTEXITCODE $(if ($scOut) { $scOut -join ' | ' } else { '' })"
    $deadline = (Get-Date).AddSeconds(20)
    while ((Get-Date) -lt $deadline) {
        if (-not (Get-Service -Name $Name -ErrorAction SilentlyContinue)) {
            Write-InstallSvcLog "Ensure-Removed $Name : gone"
            return
        }
        Start-Sleep -Milliseconds 500
    }
    throw "Could not remove existing service $Name (run this script as Administrator)"
}

function Install-OneService {
    param(
        [string]$Name,
        [string]$DisplayName,
        [string]$Description,
        [string]$Exe,
        [string]$AppParameters,
        [string]$LogPrefix
    )

    if (-not (Test-Path $Exe)) {
        throw "Executable not found: $Exe"
    }

    $appDir = Split-Path -Parent $Exe
    Ensure-Removed $Name

    Invoke-Nssm install $Name $Exe
    Start-Sleep -Milliseconds 500

    if (-not (Get-Service -Name $Name -ErrorAction SilentlyContinue)) {
        throw "Service not created after nssm install: $Name"
    }

    Invoke-Nssm set $Name AppDirectory $appDir
    if ($AppParameters) {
        Invoke-Nssm set $Name AppParameters $AppParameters
    }
    Invoke-Nssm set $Name DisplayName $DisplayName
    Invoke-Nssm set $Name Description $Description
    Invoke-Nssm set $Name Start SERVICE_AUTO_START
    Invoke-Nssm set $Name ObjectName LocalSystem
<<<<<<< HEAD
    Invoke-Nssm set $Name AppEnvironmentExtra "PYTHONUTF8=1`nPYTHONIOENCODING=utf-8"
=======
>>>>>>> 3fa2e1c7c126607004b404060edf4d5e3dc3bd97
    Invoke-Nssm set $Name AppStdout (Join-Path $logDir "$LogPrefix-stdout.log")
    Invoke-Nssm set $Name AppStderr (Join-Path $logDir "$LogPrefix-stderr.log")
    Invoke-Nssm set $Name AppRotateFiles 1
    Invoke-Nssm set $Name AppRotateBytes 1048576

    try {
        Start-Service -Name $Name -ErrorAction Stop
    } catch {
        Write-InstallSvcLog "Start-Service $Name warning: $_ (check $($LogPrefix)-stderr.log)"
    }

    $svc = Get-Service -Name $Name
    Write-Host "[install] Registered service: $Name ($DisplayName) Status=$($svc.Status)"
    Write-InstallSvcLog "done $Name Status=$($svc.Status)"
}

if ($InstallBridge) {
    $bridgeExe = Join-Path $AppDir "Bridge\cornerstone-bridge.exe"
    $bridgeCfg = Join-Path $ConfigDir "cornerstone-bridge.config.json"
    if (-not (Test-Path $bridgeCfg)) { throw "Config not found: $bridgeCfg" }
    $bridgeArgs = '-c "{0}"' -f $bridgeCfg
    Install-OneService -Name "CornerstoneBridge" `
        -DisplayName "Cornerstone Mock Bridge" `
        -Description "Cornerstone Mock TCP gateway and REST API" `
        -Exe $bridgeExe `
        -AppParameters $bridgeArgs `
        -LogPrefix "bridge"
}

if ($InstallWeb) {
    $webExe = Join-Path $AppDir "Web\cornerstone-web.exe"
    $webCfg = Join-Path $ConfigDir "cornerstone-web.config.json"
    if (-not (Test-Path $webCfg)) { throw "Config not found: $webCfg" }
    $webArgs = '-c "{0}"' -f $webCfg
    Install-OneService -Name "CornerstoneWeb" `
        -DisplayName "Cornerstone Mock Web" `
        -Description "Cornerstone Mock web UI" `
        -Exe $webExe `
        -AppParameters $webArgs `
        -LogPrefix "web"
}

if (-not $InstallBridge -and -not $InstallWeb) {
    Write-Warning "No -InstallBridge or -InstallWeb; nothing registered."
}

Write-InstallSvcLog "finished"
