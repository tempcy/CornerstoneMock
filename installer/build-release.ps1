# Build all executables and optional Inno Setup installer
# Usage: .\build-release.ps1
param(
    [switch]$SkipPython,
    [switch]$SkipQueue,
    [switch]$SkipInstaller,
    [switch]$BridgeOnly,
    [string]$Configuration = "Release"
)

if ($BridgeOnly) {
    $SkipQueue = $true
    $SkipInstaller = $true
}

$ErrorActionPreference = "Stop"
$AppVersion = (Get-Content -LiteralPath (Join-Path (Split-Path -Parent $PSScriptRoot) "VERSION") -Raw).Trim()
if (-not $AppVersion) { $AppVersion = "0.2.0" }
if ($PSVersionTable.PSVersion.Major -ge 6) {
    $OutputEncoding = [System.Text.UTF8Encoding]::new($false)
}
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)

$Root = Split-Path -Parent $PSScriptRoot
$InstallerDir = $PSScriptRoot
$Staging = Join-Path $InstallerDir "staging"
# 安装包输出到本机 LocalAppData，避免仓库在 OneDrive/网盘同步时生成「云占位符」导致安装报 corrupted
$Dist = Join-Path $env:LOCALAPPDATA "CornerstoneMock\installer-dist"
$DistRepo = Join-Path $InstallerDir "dist"
$Tools = Join-Path $InstallerDir "tools"

function Ensure-Dir($p) {
    New-Item -ItemType Directory -Force -Path $p | Out-Null
}

function Resolve-BuildPython {
    # Return Python 3.14+ executable for venv / PyInstaller.
    try {
        $exe = (& py -3.14 -c "import sys; print(sys.executable)" 2>$null | Out-String).Trim()
        if ($exe -and (Test-Path -LiteralPath $exe)) { return $exe }
    } catch { }

    $cmd = Get-Command python -ErrorAction SilentlyContinue
    if ($cmd) {
        $ver = (& $cmd.Source -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>$null | Out-String).Trim()
        if ($ver -match '^3\.(1[4-9]|[2-9][0-9])$') { return $cmd.Source }
    }

    throw @"
Python 3.14+ required. Install Python 3.14, set it as default, or run:
  py -3.14 -m venv installer\.venv-build
Then re-run build-release.ps1
"@
}

function Test-VenvPythonVersion([string]$PythonExe) {
    $ver = (& $PythonExe -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>$null | Out-String).Trim()
    return ($ver -match '^3\.(1[4-9]|[2-9][0-9])$')
}

function Convert-IssDefinePath([string]$Path) {
    return (Resolve-Path -LiteralPath $Path).Path.Replace('\', '/')
}

function Get-BuildId([string]$RepoRoot) {
    # 每次打包唯一标识：UTC 时间戳 + Git 短哈希（无 Git 时仅时间戳）
    $ts = (Get-Date).ToUniversalTime().ToString("yyyyMMddHHmmss")
    $hash = $null
    Push-Location $RepoRoot
    try {
        $hash = (git rev-parse --short=7 HEAD 2>$null | Out-String).Trim()
    } finally {
        Pop-Location
    }
    if ($hash) { return "${ts}-${hash}" }
    return $ts
}

function Write-BuildInfo {
    param(
        [string]$StagingRoot,
        [string]$Version,
        [string]$BuildId
    )
    $info = [ordered]@{
        version  = $Version
        build_id = $BuildId
        built_at = (Get-Date).ToUniversalTime().ToString("o")
    }
    $path = Join-Path $StagingRoot "build-info.json"
    $json = ($info | ConvertTo-Json -Compress) + "`n"
    [IO.File]::WriteAllText($path, $json, [Text.UTF8Encoding]::new($false))
}

function Test-InstallerExeReady([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path)) {
        throw "Installer not found: $Path"
    }
    $item = Get-Item -LiteralPath $Path -Force
    if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) {
        throw @"
Installer is a cloud-sync placeholder (ReparsePoint), not a real file: $Path
Inno Setup will report 'The setup files are corrupted'.
Use the copy under %LOCALAPPDATA%\CornerstoneMock\installer-dist\ or run build-release.ps1 again.
If you copy into the repo, right-click the .exe -> 'Always keep on this device' / 始终保留在此设备上.
"@
    }
    if ($item.Length -lt 1MB) {
        throw "Installer too small ($($item.Length) bytes): $Path"
    }
}

function Publish-InstallerToRepo([string]$SourceExe) {
    Ensure-Dir $DistRepo
    $dest = Join-Path $DistRepo (Split-Path -Leaf $SourceExe)
    $bytes = [IO.File]::ReadAllBytes($SourceExe)
    [IO.File]::WriteAllBytes($dest, $bytes)
    $item = Get-Item -LiteralPath $dest -Force
    if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) {
        Write-Warning "[build] $dest is still a cloud placeholder; run installer from: $SourceExe"
        return
    }
    Write-Host "[build] Copied installer to repo: $dest"
}

function Write-InstallScript {
    param([string]$SourcePath, [string]$DestPath)
    $text = [System.IO.File]::ReadAllText($SourcePath)
    $text = $text -replace "`r`n", "`n" -replace "`n", "`r`n"
    $utf8Bom = New-Object System.Text.UTF8Encoding $true
    [System.IO.File]::WriteAllText($DestPath, $text, $utf8Bom)
}

function Test-QueueExecutable {
    param([string]$QueueDir)
    $exe = Join-Path $QueueDir "CornerstoneQueue.exe"
    if (-not (Test-Path $exe)) { throw "Queue smoke test: missing $exe" }
    $p = Start-Process -FilePath $exe -WorkingDirectory $QueueDir -PassThru
    Start-Sleep -Seconds 3
    if ($p.HasExited) {
        $code = $p.ExitCode
        $hex = ('0x{0:X8}' -f ([uint32]($code -band 0xffffffff)))
        throw "Queue smoke test failed (exit $code $hex). Release build cannot start; check runtime or language packs."
    }
    Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue
    Write-Host "[build] Queue smoke test OK"
}

function Trim-QueueLanguageFolders {
    param([string]$QueueDir)
    # WASDK MUI：保留 en-us、zh-CN；zh-Hans 为简体中文系统上 WinUI 常用回退（不可删）
    $queueFixedDirNames = @('Assets', 'Microsoft.UI.Xaml', 'runtimes')
    $keepLanguageNames = @('en-us', 'zh-CN', 'zh-Hans')
    foreach ($dir in Get-ChildItem -Path $QueueDir -Directory -ErrorAction SilentlyContinue) {
        if ($queueFixedDirNames -contains $dir.Name) { continue }
        if ($keepLanguageNames -contains $dir.Name) { continue }
        Write-Host "[build] Queue: remove language folder $($dir.Name)"
        Remove-Item $dir.FullName -Recurse -Force
    }
}

function Stop-CornerstoneMockRuntime {
    param([string]$StagingRoot)

    foreach ($svcName in @("CornerstoneBridge", "CornerstoneWeb")) {
        $svc = Get-Service -Name $svcName -ErrorAction SilentlyContinue
        if (-not $svc) { continue }
        if ($svc.Status -ne "Stopped") {
            Write-Host "[build] Stop service $svcName ..."
            Stop-Service -Name $svcName -Force -ErrorAction SilentlyContinue
            $deadline = (Get-Date).AddSeconds(15)
            while ((Get-Date) -lt $deadline) {
                if ((Get-Service -Name $svcName -ErrorAction SilentlyContinue).Status -eq "Stopped") { break }
                Start-Sleep -Milliseconds 400
            }
        }
    }

    $exeNames = @("cornerstone-bridge", "cornerstone-bridge-ui", "cornerstone-web", "cornerstone-cli", "CornerstoneQueue")
    foreach ($name in $exeNames) {
        Get-Process -Name $name -ErrorAction SilentlyContinue | ForEach-Object {
            $path = $_.Path
            $underStaging = $StagingRoot -and $path -and ($path.StartsWith($StagingRoot, [StringComparison]::OrdinalIgnoreCase))
            if ($underStaging -or -not $StagingRoot) {
                Write-Host "[build] Stop process $($_.ProcessName) (PID $($_.Id))"
                Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue
            }
        }
    }
    Start-Sleep -Milliseconds 800
}

function Remove-BuildTree([string]$Path) {
    if (-not (Test-Path $Path)) { return }
    try {
        Remove-Item $Path -Recurse -Force -ErrorAction Stop
    } catch {
        Write-Warning "[build] Could not remove $Path on first try: $($_.Exception.Message)"
        Stop-CornerstoneMockRuntime -StagingRoot $Staging
        Start-Sleep -Seconds 1
        Remove-Item $Path -Recurse -Force
    }
}

function Ensure-Nssm {
    $nssm = Join-Path $Tools "nssm.exe"
    if (Test-Path $nssm) { return $nssm }
    Ensure-Dir $Tools
    $zip = Join-Path $Tools "nssm.zip"
    $url = "https://nssm.cc/release/nssm-2.24.zip"
    Write-Host "[build] Downloading NSSM ..."
    Invoke-WebRequest -Uri $url -OutFile $zip -UseBasicParsing
    Expand-Archive -Path $zip -DestinationPath $Tools -Force
    $cand = Get-ChildItem -Path $Tools -Recurse -Filter "nssm.exe" |
        Where-Object { $_.FullName -match "win64" } |
        Select-Object -First 1
    if (-not $cand) {
        $cand = Get-ChildItem -Path $Tools -Recurse -Filter "nssm.exe" | Select-Object -First 1
    }
    if (-not $cand) { throw "nssm.exe not found after extract" }
    Copy-Item $cand.FullName $nssm -Force
    return $nssm
}

$BuildId = Get-BuildId -RepoRoot $Root

Write-Host "[build] Version $AppVersion, build id $BuildId"
Write-Host "[build] Clean staging / dist ..."
Stop-CornerstoneMockRuntime -StagingRoot $Staging
Remove-BuildTree $Staging
Remove-BuildTree $Dist
Ensure-Dir $Staging
Ensure-Dir $Dist
# 删除仓库内旧的云占位符安装包，避免误运行
Get-ChildItem $DistRepo -Filter "CornerstoneMock-Setup-*.exe" -ErrorAction SilentlyContinue |
    Remove-Item -Force -ErrorAction SilentlyContinue

# --- Python / PyInstaller ---
if (-not $SkipPython) {
    $venv = Join-Path $InstallerDir ".venv-build"
    $py = Join-Path $venv "Scripts\python.exe"
    $hostPython = Resolve-BuildPython
    Write-Host "[build] Host Python: $hostPython"

    if ((Test-Path $py) -and -not (Test-VenvPythonVersion $py)) {
        Write-Host "[build] Recreate venv (requires Python 3.14+) ..."
        Remove-Item $venv -Recurse -Force -ErrorAction SilentlyContinue
    }

    if (-not (Test-Path $py)) {
        Write-Host "[build] Create venv with Python 3.14 ..."
        & $hostPython -m venv $venv
        if (-not (Test-Path $py)) { throw "venv creation failed: $py" }
        if (-not (Test-VenvPythonVersion $py)) { throw "venv Python is not 3.14+: $py" }
        & $py -m pip install -U pip wheel setuptools
    }

    Write-Host "[build] Install packages and PyInstaller ..."
    & $py -m pip install -U pip
    & $py -m pip install pyinstaller
    & $py -m pip install -e (Join-Path $Root "CornerstoneCLI")
    & $py -m pip install -e (Join-Path $Root "CornerstoneBridge")
    & $py -m pip install -e (Join-Path $Root "CornerstoneWeb")
    & $py -m pip install -e (Join-Path $Root "CornerstoneBridge[ui]")

    $specDir = Join-Path $InstallerDir "specs"
    $pyDist = Join-Path $InstallerDir "pydist"
    $pyWork = Join-Path $InstallerDir "pywork"
    if (Test-Path $pyDist) { Remove-Item $pyDist -Recurse -Force }
    if (Test-Path $pyWork) { Remove-Item $pyWork -Recurse -Force }
    Ensure-Dir $pyDist
    Ensure-Dir $pyWork

    $pyTargets = if ($BridgeOnly) { @("bridge") } else { @("bridge", "web", "cli") }
    foreach ($name in $pyTargets) {
        $specFile = (Join-Path $specDir "$name.spec")
        Write-Host "[build] PyInstaller $name ..."
        # .spec 已给定路径时不可再传 --specpath
        & $py -m PyInstaller $specFile --noconfirm --distpath $pyDist --workpath $pyWork
        if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed: $name (exit $LASTEXITCODE)" }
    }

    # PyInstaller 输出目录展平到 Bridge / Web / CLI（exe 在第一层，无 cornerstone-* 子目录）
    $bridgeOut = Join-Path $Staging "Bridge"
    $srcDir = Join-Path $pyDist "cornerstone-bridge"
    if (Test-Path $bridgeOut) { Remove-Item $bridgeOut -Recurse -Force }
    Ensure-Dir $bridgeOut
    Copy-Item (Join-Path $srcDir "*") $bridgeOut -Recurse -Force

    if (-not $BridgeOnly) {
        $webOut = Join-Path $Staging "Web"
        $cliOut = Join-Path $Staging "CLI"
        foreach ($pair in @(
                @{ Src = "cornerstone-web"; Dst = $webOut },
                @{ Src = "cornerstone-cli"; Dst = $cliOut }
            )) {
            $src = Join-Path $pyDist $pair.Src
            if (Test-Path $pair.Dst) { Remove-Item $pair.Dst -Recurse -Force }
            Ensure-Dir $pair.Dst
            Copy-Item (Join-Path $src "*") $pair.Dst -Recurse -Force
        }
    }

    $cfgDir = Join-Path $Staging "config"
    Ensure-Dir $cfgDir
    Copy-Item (Join-Path $Root "CornerstoneBridge\cornerstone-bridge.config.example.json") `
        (Join-Path $cfgDir "cornerstone-bridge.config.example.json") -Force
    if (-not $BridgeOnly) {
        Copy-Item (Join-Path $Root "CornerstoneWeb\cornerstone-web.config.example.json") `
            (Join-Path $cfgDir "cornerstone-web.config.example.json") -Force
    }
}

if ($BridgeOnly) {
    $bridgeStaging = Join-Path $Staging "Bridge"
    # 写入 Bridge\，与 Compress-Archive 范围一致（现场覆盖 Bridge 目录时可读到 build-info.json）
    Write-BuildInfo -StagingRoot $bridgeStaging -Version $AppVersion -BuildId $BuildId
    $zipPath = Join-Path $Dist "CornerstoneBridge-$AppVersion-$BuildId-win64.zip"
    if (Test-Path $zipPath) { Remove-Item $zipPath -Force }
    Compress-Archive -Path (Join-Path $bridgeStaging "*") -DestinationPath $zipPath -Force
    Write-Host ""
    Write-Host "[build] Bridge-only package ready:"
    Write-Host "  Folder: $bridgeStaging"
    Write-Host "  Zip:    $zipPath"
    Write-Host "  Field:  stop CornerstoneBridge service, copy folder over"
    Write-Host "          'C:\Program Files\CornerstoneMock\Bridge\' then restart service."
    Write-Host ""
    Write-Host "[build] staging: $bridgeStaging"
    exit 0
}

# --- CornerstoneQueue (WinUI) ---
if (-not $SkipQueue) {
    $queueProj = Join-Path $Root "CornerstoneQueue\CornerstoneQueue\CornerstoneQueue.csproj"
    $queuePub = Join-Path $Staging "Queue"
    Ensure-Dir $queuePub
    Write-Host "[build] dotnet publish CornerstoneQueue (self-contained) ..."
    # 仪器机常无 .NET 8 / 全局 WASDK；与 csproj WindowsAppSDKSelfContained 一并打包运行时
    # 语言列表用 %3B 代替 ;（PowerShell 与 MSBuild 都会把裸分号当分隔符）；勿用反引号续行（易被解析成独立命令）
    dotnet publish $queueProj -c $Configuration -r win-x64 --self-contained true -o $queuePub "-p:SatelliteResourceLanguages=en-US%3Bzh-CN%3Bzh-Hans"
    if ($LASTEXITCODE -ne 0) { throw "dotnet publish failed (exit $LASTEXITCODE)" }
    Trim-QueueLanguageFolders -QueueDir $queuePub
    Test-QueueExecutable -QueueDir $queuePub
}

# --- NSSM + scripts ---
Ensure-Nssm | Out-Null
Ensure-Dir (Join-Path $Staging "tools")
Ensure-Dir (Join-Path $Staging "scripts")
Copy-Item (Join-Path $Tools "nssm.exe") (Join-Path $Staging "tools\nssm.exe") -Force
Ensure-Dir (Join-Path $Staging "scripts")
foreach ($scriptName in @(
        "install-services.ps1",
        "uninstall-services.ps1",
        "post-install.ps1",
        "merge-config.ps1",
        "validate-install.ps1",
        "register-services.cmd"
    )) {
    $src = Join-Path $InstallerDir "scripts\$scriptName"
    if (-not (Test-Path $src)) { throw "Missing installer script: $src" }
    Write-InstallScript -SourcePath $src -DestPath (Join-Path $Staging "scripts\$scriptName")
}

Write-BuildInfo -StagingRoot $Staging -Version $AppVersion -BuildId $BuildId

# --- Inno Setup ---
if (-not $SkipInstaller) {
    $iscc = @(
        "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
        "${env:ProgramFiles}\Inno Setup 6\ISCC.exe"
    ) | Where-Object { Test-Path $_ } | Select-Object -First 1

    if (-not $iscc) {
        Write-Warning "[build] Inno Setup 6 (ISCC.exe) not found. Install from https://jrsoftware.org/isinfo.php"
        Write-Host "[build] Staging ready: $Staging"
        exit 0
    }

    Write-Host "[build] Compile installer (output: $Dist) ..."
    $stagingIss = Convert-IssDefinePath $Staging
    $distIss = Convert-IssDefinePath $Dist
    & $iscc (Join-Path $InstallerDir "Cornerstone.iss") "/DStagingRoot=$stagingIss" "/DOutputDir=$distIss" "/DMyAppVersion=$AppVersion" "/DMyBuildId=$BuildId"
    if ($LASTEXITCODE -ne 0) { throw "ISCC failed (exit $LASTEXITCODE)" }

    $setupExe = Join-Path $Dist "CornerstoneMock-Setup-$AppVersion-$BuildId.exe"
    Test-InstallerExeReady $setupExe
    try {
        Publish-InstallerToRepo $setupExe
    } catch {
        Write-Warning "[build] Could not copy installer into repo dist: $_"
    }
    Write-Host "[build] Done. Run installer from: $setupExe"
}

Write-Host "[build] staging: $Staging"
