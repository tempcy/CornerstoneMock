# Pre-finish install: check local ports and privileged_add_samples_host
param(
    [Parameter(Mandatory = $true)]
    [string]$ConfigDir,
    [string]$InstallBridge = "1",
    [string]$InstallWeb = "1",
    [switch]$NonInteractive
)

$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Windows.Forms | Out-Null

function Read-JsonFile([string]$Path) {
    if (-not (Test-Path $Path)) { return $null }
    $raw = Get-Content -Path $Path -Raw -Encoding UTF8
    if ([string]::IsNullOrWhiteSpace($raw)) { return $null }
    return $raw | ConvertFrom-Json
}

function Test-PortFree([int]$Port) {
    if ($Port -lt 1 -or $Port -gt 65535) { return $false, "端口号无效" }
    $owner = ""
    try {
        $conns = @(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)
        if ($conns.Count -gt 0) {
            $owningPid = $conns[0].OwningProcess
            if ($owningPid) {
                $proc = Get-Process -Id $owningPid -ErrorAction SilentlyContinue
                if ($proc) {
                    $owner = '{0} (PID {1})' -f $proc.ProcessName, $owningPid
                } else {
                    $owner = "PID $owningPid"
                }
            }
            if ($owner) { return $false, "已被占用: $owner" }
            return $false, "已被占用"
        }
    } catch {
        # fall through to bind test
    }
    try {
        $l = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Any, $Port)
        $l.Start()
        $l.Stop()
        return $true, ""
    } catch {
        return $false, $_.Exception.Message
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
    $bridgePath = Join-Path $ConfigDir "cornerstone-bridge.config.json"
    $webPath = Join-Path $ConfigDir "cornerstone-web.config.json"

    if ($InstallBridge -eq "1" -and $BridgeCfg) {
        $ok, $why = Test-PortFree -Port ([int]$BridgeCfg.port)
        if (-not $ok) {
            $issues.Add("TCP 网关端口 $($BridgeCfg.port) (cornerstone-bridge.config.json -> port) $why")
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
            $issues.Add("Web 监听端口 $($WebCfg.web_port) (cornerstone-web.config.json -> web_port) $why3")
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

$bridgeCfgPath = Join-Path $ConfigDir "cornerstone-bridge.config.json"
$webCfgPath = Join-Path $ConfigDir "cornerstone-web.config.json"
$bridge = Read-JsonFile $bridgeCfgPath
$web = Read-JsonFile $webCfgPath

if ($NonInteractive) {
    $issues, $bp, $wp = Collect-Issues -BridgeCfg $bridge -WebCfg $web
    $logDir = Join-Path $ConfigDir "logs"
    New-Item -ItemType Directory -Force -Path $logDir | Out-Null
    $logPath = Join-Path $logDir "validate-install.log"
    if ($issues.Count -eq 0) {
        Add-Content -Path $logPath -Value "$(Get-Date -Format o) OK" -Encoding UTF8
        exit 0
    }
    $body = @("Port/network warnings (install continues):", "") + $issues
    Add-Content -Path $logPath -Value "$(Get-Date -Format o) $($body -join [Environment]::NewLine)" -Encoding UTF8
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
            $bridge = Read-JsonFile $bridgeCfgPath
            $web = Read-JsonFile $webCfgPath
        }
        ([System.Windows.Forms.DialogResult]::No) {
            exit 0
        }
        default {
            $bridge = Read-JsonFile $bridgeCfgPath
            $web = Read-JsonFile $webCfgPath
        }
    }
}
