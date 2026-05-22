# Post-install: merge/migrate configs, port check, register Windows services (visible console).
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

trap {
    Write-Host ""
    Write-Host "安装后步骤失败: $_" -ForegroundColor Red
    Write-Host "日志: $(Join-Path $ConfigDir 'logs\post-install.log')"
    Read-Host "按 Enter 关闭此窗口"
    exit 1
}

try { chcp 65001 | Out-Null } catch { }
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$Host.UI.RawUI.WindowTitle = "Cornerstone Mock - 安装后配置与服务"
Write-Host ""
Write-Host "=== Cornerstone Mock：配置合并与服务注册 ===" -ForegroundColor Cyan
Write-Host "AppDir:   $AppDir"
Write-Host "ConfigDir: $ConfigDir"
Write-Host ""

. (Join-Path $PSScriptRoot "merge-config.ps1")

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

function Write-Step {
    param([string]$Message)
    Write-Host $Message
    Write-InstallLog $Message
}

Write-Step "post-install start InstallBridgeSvc=$InstallBridgeSvc InstallWebSvc=$InstallWebSvc"

New-Item -ItemType Directory -Force -Path $ConfigDir | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $ConfigDir "logs") | Out-Null

$legacyConfigDir = Join-Path ${env:ProgramData} "CornerstoneMock"
$bridgeExample = Join-Path $AppDir "config\cornerstone-bridge.config.example.json"
$webExample = Join-Path $AppDir "config\cornerstone-web.config.example.json"
$bridgeCfg = Join-Path $ConfigDir "cornerstone-bridge.config.json"
$webCfg = Join-Path $ConfigDir "cornerstone-web.config.json"
$queueJson = Join-Path $ConfigDir "cornerstone-bridge.add-samples-queue.json"

Write-Step "--- 1/3 合并配置文件 ---"

if ($InstallBridge -eq "1" -and (Test-Path $bridgeExample)) {
    $legacyBridge = Join-Path $legacyConfigDir "cornerstone-bridge.config.json"
    if (Import-LegacyConfigIfNeeded -TargetPath $bridgeCfg -LegacyPath $legacyBridge) {
        Write-Step "已从 ProgramData 迁移 bridge 配置"
    }
    if (Test-Path $bridgeCfg) {
        Merge-JsonConfigFile -TargetPath $bridgeCfg -TemplatePath $bridgeExample
        Write-Step "已合并 bridge 配置: $bridgeCfg"
    } else {
        Copy-Item $bridgeExample $bridgeCfg -Force
        Write-Step "已新建 bridge 配置: $bridgeCfg"
    }
}

if ($InstallWeb -eq "1" -and (Test-Path $webExample)) {
    $legacyWeb = Join-Path $legacyConfigDir "cornerstone-web.config.json"
    if (Import-LegacyConfigIfNeeded -TargetPath $webCfg -LegacyPath $legacyWeb) {
        Write-Step "已从 ProgramData 迁移 web 配置"
    }
    if (Test-Path $webCfg) {
        Merge-JsonConfigFile -TargetPath $webCfg -TemplatePath $webExample
        Write-Step "已合并 web 配置: $webCfg"
    } else {
        Copy-Item $webExample $webCfg -Force
        Write-Step "已新建 web 配置: $webCfg"
    }
}

if (-not (Test-Path $queueJson) -and (Test-Path (Join-Path $legacyConfigDir "cornerstone-bridge.add-samples-queue.json"))) {
    Copy-Item (Join-Path $legacyConfigDir "cornerstone-bridge.add-samples-queue.json") $queueJson -Force
    Write-Step "已迁移样品队列文件"
}
if ((Test-Path $bridgeCfg) -and -not (Test-Path $queueJson)) {
    $utf8NoBom = New-Object System.Text.UTF8Encoding $false
    [System.IO.File]::WriteAllText($queueJson, '{"version":1,"items":[]}', $utf8NoBom)
    Write-Step "已创建空队列文件"
}

$validate = Join-Path $AppDir "scripts\validate-install.ps1"
Write-Step "--- 2/3 端口与网络检查 ---"
if (Test-Path $validate) {
    try {
        & $validate -ConfigDir $ConfigDir -InstallBridge $InstallBridge -InstallWeb $InstallWeb -NonInteractive
        Write-Step "validate-install 完成 exit=$LASTEXITCODE"
    } catch {
        Write-Host "validate-install 警告: $_" -ForegroundColor Yellow
        Write-InstallLog "validate-install warning: $_"
    }
} else {
    Write-Step "未找到 validate-install.ps1，已跳过"
}

$doBridge = $InstallBridgeSvc -eq "1"
$doWeb = $InstallWebSvc -eq "1"
Write-Step "--- 3/3 注册 Windows 服务 (Bridge=$doBridge Web=$doWeb) ---"

if ($doBridge -or $doWeb) {
    $installer = Join-Path $AppDir "scripts\install-services.ps1"
    if (-not (Test-Path $installer)) {
        throw "install-services.ps1 not found: $installer"
    }

    $invokeParams = @{ AppDir = $AppDir; ConfigDir = $ConfigDir }
    if ($doBridge) { $invokeParams.InstallBridge = $true }
    if ($doWeb) { $invokeParams.InstallWeb = $true }

    if (Test-IsAdministrator) {
        Write-Host "以管理员身份运行 install-services.ps1 ..." -ForegroundColor Gray
        & $installer @invokeParams
        $svcExit = $LASTEXITCODE
    } else {
        Write-Host "请求管理员权限运行 install-services.ps1 ..." -ForegroundColor Yellow
        $svcArgs = @(
            "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $installer,
            "-AppDir", $AppDir, "-ConfigDir", $ConfigDir
        )
        if ($doBridge) { $svcArgs += "-InstallBridge" }
        if ($doWeb) { $svcArgs += "-InstallWeb" }
        $proc = Start-Process -FilePath "powershell.exe" -ArgumentList $svcArgs -Verb RunAs -Wait -PassThru
        $svcExit = $proc.ExitCode
    }

    Write-Step "install-services.ps1 exit=$svcExit"
    if ($svcExit -ne 0) {
        $detail = Get-Content (Join-Path $ConfigDir "logs\install-services.log") -ErrorAction SilentlyContinue | Select-Object -Last 15
        throw "install-services 失败 (exit $svcExit). $(if ($detail) { $detail -join ' ; ' } else { '见 install-services.log' })"
    }

    foreach ($svcName in @($(if ($doBridge) { "CornerstoneBridge" }), $(if ($doWeb) { "CornerstoneWeb" }))) {
        if (-not $svcName) { continue }
        $svc = Get-Service -Name $svcName -ErrorAction SilentlyContinue
        if (-not $svc) { throw "服务未注册: $svcName" }
        Write-Step "服务 $svcName 状态: $($svc.Status)"
    }
} else {
    Write-Step "未勾选服务任务，跳过服务注册"
}

Write-Host ""
Write-Host "=== 安装后步骤完成 ===" -ForegroundColor Green
Write-Host "日志: $(Join-Path $ConfigDir 'logs\post-install.log')"
Write-Step "post-install done"
