# Pre-finish install: check local ports and privileged_add_samples_host
param(
    [Parameter(Mandatory = $true)]
    [string]$ConfigDir,
    [string]$InstallBridge = "1",
    [string]$InstallWeb = "1",
    [switch]$NonInteractive
)

$ErrorActionPreference = "Stop"

. (Join-Path $PSScriptRoot "merge-config.ps1")

function Write-ValidateLog {
    param([string]$Message)
    Write-Host "  $Message"
    $logDir = Join-Path $ConfigDir "logs"
    New-Item -ItemType Directory -Force -Path $logDir | Out-Null
    Add-Content -Path (Join-Path $logDir "validate-install.log") -Value "$(Get-Date -Format o) $Message" -Encoding UTF8
}

function Read-JsonFile([string]$Path) {
    if (-not (Test-Path $Path)) { return $null }
    return Read-JsonConfigFile $Path
}

function Resolve-BridgeConfigPath {
    $toml = Join-Path $ConfigDir "cornerstone-bridge.config.toml"
    $json = Join-Path $ConfigDir "cornerstone-bridge.config.json"
    if (Test-Path $toml) { return $toml }
    if (Test-Path $json) { return $json }
    return $toml
}

function Resolve-WebConfigPath {
    $toml = Join-Path $ConfigDir "cornerstone-web.config.toml"
    $json = Join-Path $ConfigDir "cornerstone-web.config.json"
    if (Test-Path $toml) { return $toml }
    if (Test-Path $json) { return $json }
    return $toml
}

function Read-FlatTomlFile([string]$Path) {
    if (-not (Test-Path $Path)) { return $null }
    $data = @{}
    foreach ($line in Get-Content -Path $Path -Encoding UTF8) {
        $t = $line.Trim()
        if ($t -eq "" -or $t.StartsWith("#")) { continue }
        if ($t.StartsWith("[")) { continue }
        if ($t -match '^([A-Za-z0-9_]+)\s*=\s*(.+)$') {
            $key = $Matches[1]
            $val = $Matches[2].Trim()
            if ($val.Length -ge 2 -and $val.StartsWith('"') -and $val.EndsWith('"')) {
                $val = $val.Substring(1, $val.Length - 2)
            }
            if ($val -eq "true") { $data[$key] = $true }
            elseif ($val -eq "false") { $data[$key] = $false }
            elseif ($val -match '^\d+$') { $data[$key] = [int]$val }
            elseif ($val -match '^\d+(\.\d+)?$') { $data[$key] = [double]$val }
            else { $data[$key] = $val }
        }
    }
    if ($data.Count -eq 0) { return $null }
    return [PSCustomObject]$data
}

function Read-BridgeConfigFile {
    $path = Resolve-BridgeConfigPath
    if (-not (Test-Path $path)) { return $null, $path }
    if ($path -like "*.toml") {
        return (Read-FlatTomlFile $path), $path
    }
    return (Read-JsonFile $path), $path
}

function Read-WebConfigFile {
    $path = Resolve-WebConfigPath
    if (-not (Test-Path $path)) { return $null, $path }
    if ($path -like "*.toml") {
        return (Read-FlatTomlFile $path), $path
    }
    return (Read-JsonFile $path), $path
}

function Test-PortFree([int]$Port) {
    if ($Port -lt 1 -or $Port -gt 65535) { return $false, "端口号无效" }
    try {
        $l = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Any, $Port)
        $l.Start()
        $l.Stop()
        return $true, ""
    } catch {
        $msg = $_.Exception.Message
        if ($msg -match "address already in use|已在使用中|10048") {
            return $false, "已被占用"
        }
        return $false, $msg
    }
}

function Test-PrivilegedHost([string]$Value) {
    $ip = $null
    if ([string]::IsNullOrWhiteSpace($Value)) {
        return $false, "未设置（无法按 IP 匹配 AddSamples 直通上位机）"
    }
    if (-not [System.Net.IPAddress]::TryParse($Value.Trim(), [ref]$ip)) {
        return $false, "不是合法的 IP 地址"
    }
    $t = $Value.Trim().ToLowerInvariant()
    if ($t -eq "0.0.0.0") {
        return $false, "不能为 0.0.0.0，请填写仪器客户端 IP"
    }
    if ($t -eq "127.0.0.1" -or $t -eq "localhost") {
        return $true, "当前为回环地址；若仪器 TCP 客户端在其他电脑，请改为该电脑局域网 IP"
    }
    return $true, ""
}

function Collect-Issues {
    param($BridgeCfg, $WebCfg)

    $issues = New-Object System.Collections.Generic.List[string]
    $bridgePath = Resolve-BridgeConfigPath
    $webPath = Resolve-WebConfigPath

    if ($InstallBridge -eq "1" -and $BridgeCfg) {
        $ok, $why = Test-PortFree -Port ([int]$BridgeCfg.port)
        if (-not $ok) {
            $issues.Add("TCP 网关端口 $($BridgeCfg.port) (bridge 配置 -> port) $why")
        }
        $ok2, $why2 = Test-PortFree -Port ([int]$BridgeCfg.bridge_api_port)
        if (-not $ok2) {
            $issues.Add("Bridge REST 端口 $($BridgeCfg.bridge_api_port) (-> bridge_api_port) $why2")
        }
        $ipOk, $ipNote = Test-PrivilegedHost -Value ([string]$BridgeCfg.privileged_add_samples_host)
        if (-not $ipOk) {
            $issues.Add("特权 AddSamples IP (-> privileged_add_samples_host): $ipNote")
        } elseif ($ipNote) {
            $issues.Add("特权 AddSamples IP (-> privileged_add_samples_host): $ipNote")
        }
    }

    if ($InstallWeb -eq "1" -and $WebCfg) {
        $ok3, $why3 = Test-PortFree -Port ([int]$WebCfg.web_port)
        if (-not $ok3) {
            $issues.Add("Web 监听端口 $($WebCfg.web_port) (web 配置 -> web_port) $why3")
        }
        if ($InstallBridge -eq "1" -and $BridgeCfg) {
            $apiPort = [int]$BridgeCfg.bridge_api_port
            $webApi = [int]$WebCfg.bridge_api_port
            if ($webApi -ne $apiPort) {
                $issues.Add("Web 的 bridge_api_port ($webApi) 与 Bridge 的 ($apiPort) 不一致")
            }
        }
    }

    return $issues, $bridgePath, $webPath
}

function Show-ValidationDialog {
    param(
        [string[]]$IssueLines,
        [string]$BridgePath,
        [string]$WebPath
    )

    Add-Type -AssemblyName System.Windows.Forms

    $lines = @(
        "安装前检查发现以下问题。请修改配置后点「取消」重新检测，或点「否」继续安装（服务可能启动失败）。",
        "",
        ($IssueLines -join [Environment]::NewLine),
        "",
        "配置文件：",
        "  Bridge: $BridgePath",
        "  Web:    $WebPath",
        "",
        "「是」= 打开配置目录",
        "「否」= 仍继续安装",
        "「取消」= 重新检测"
    )
    $body = $lines -join [Environment]::NewLine

    return [System.Windows.Forms.MessageBox]::Show(
        $body,
        "Cornerstone 安装 - 端口与网络",
        [System.Windows.Forms.MessageBoxButtons]::YesNoCancel,
        [System.Windows.Forms.MessageBoxIcon]::Warning
    )
}

Write-ValidateLog "start NonInteractive=$($NonInteractive.IsPresent)"

$bridge, $bridgeCfgPath = Read-BridgeConfigFile
$web, $webCfgPath = Read-WebConfigFile

if ($NonInteractive) {
    Write-ValidateLog "collecting issues"
    $issues, $bp, $wp = Collect-Issues -BridgeCfg $bridge -WebCfg $web
    if ($issues.Count -eq 0) {
        Write-ValidateLog "OK"
        exit 0
    }
    Write-ValidateLog ("warnings ({0}): {1}" -f $issues.Count, ($issues -join " ; "))
    exit 0
}

while ($true) {
    $issues, $bp, $wp = Collect-Issues -BridgeCfg $bridge -WebCfg $web
    if ($issues.Count -eq 0) {
        exit 0
    }

    $action = Show-ValidationDialog -IssueLines $issues.ToArray() -BridgePath $bp -WebPath $wp
    switch ($action) {
        ([System.Windows.Forms.DialogResult]::Yes) {
            if (Test-Path $ConfigDir) {
                Start-Process explorer.exe $ConfigDir
            }
            Start-Sleep -Milliseconds 800
            $bridge, $bridgeCfgPath = Read-BridgeConfigFile
            $web, $webCfgPath = Read-WebConfigFile
        }
        ([System.Windows.Forms.DialogResult]::No) {
            exit 0
        }
        default {
            $bridge, $bridgeCfgPath = Read-BridgeConfigFile
            $web, $webCfgPath = Read-WebConfigFile
        }
    }
}
