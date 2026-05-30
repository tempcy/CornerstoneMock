# 合并用户配置与安装包模板；将旧版 JSON 迁移为 TOML（样品队列 JSON 不在此脚本处理）。

function Read-JsonConfigFile {
    param([Parameter(Mandatory = $true)][string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) { return $null }
    $raw = Get-Content -LiteralPath $Path -Raw -Encoding UTF8
    $lines = $raw -split "`r?`n" | Where-Object { $_.Trim() -notmatch '^\s*//' }
    $clean = ($lines -join "`n").Trim()
    if ([string]::IsNullOrWhiteSpace($clean)) { return $null }
    return $clean | ConvertFrom-Json
}

function Read-FlatTomlHashtable {
    param([Parameter(Mandatory = $true)][string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) { return $null }
    $data = [ordered]@{}
    foreach ($line in Get-Content -LiteralPath $Path -Encoding UTF8) {
        $t = $line.Trim()
        if ($t -eq "" -or $t.StartsWith("#")) { continue }
        if ($t.StartsWith("[")) { continue }
        if ($t -match '^([A-Za-z0-9_]+)\s*=\s*(.+)$') {
            $key = $Matches[1]
            $val = $Matches[2].Trim()
            if ($val.Length -ge 2 -and $val.StartsWith('"') -and $val.EndsWith('"')) {
                $val = $val.Substring(1, $val.Length - 2).Replace('\"', '"').Replace('\\', '\')
            }
            if ($val -eq "true") { $data[$key] = $true }
            elseif ($val -eq "false") { $data[$key] = $false }
            elseif ($val -match '^\d+$') { $data[$key] = [long]$val }
            elseif ($val -match '^\d+(\.\d+)?$') { $data[$key] = [double]$val }
            else { $data[$key] = $val }
        }
    }
    if ($data.Count -eq 0) { return $null }
    return $data
}

function Format-TomlScalar {
    param($Value)
    if ($null -eq $Value) { return '""' }
    if ($Value -is [bool]) {
        if ($Value) { return "true" }
        return "false"
    }
    if ($Value -is [byte] -or $Value -is [int] -or $Value -is [long] `
        -or $Value -is [float] -or $Value -is [double] -or $Value -is [decimal]) {
        return [string]$Value
    }
    $s = [string]$Value
    $s = $s.Replace('\', '\\').Replace('"', '\"')
    return '"' + $s + '"'
}

function Write-MergedTomlFile {
    param(
        [Parameter(Mandatory = $true)][string]$TargetPath,
        [Parameter(Mandatory = $true)][string]$TemplatePath,
        [Parameter(Mandatory = $true)][hashtable]$Merged
    )
    if (-not (Test-Path -LiteralPath $TemplatePath)) {
        throw "Template not found: $TemplatePath"
    }
    $templateLines = Get-Content -LiteralPath $TemplatePath -Encoding UTF8
    $used = @{}
    $out = New-Object System.Collections.Generic.List[string]
    foreach ($line in $templateLines) {
        $t = $line.Trim()
        if ($t -match '^([A-Za-z0-9_]+)\s*=') {
            $key = $Matches[1]
            if ($Merged.Contains($key)) {
                $out.Add("$key = $(Format-TomlScalar $Merged[$key])")
                $used[$key] = $true
                continue
            }
        }
        $out.Add($line)
    }
    foreach ($key in ($Merged.Keys | Sort-Object)) {
        if (-not $used.ContainsKey($key)) {
            $out.Add("$key = $(Format-TomlScalar $Merged[$key])")
        }
    }
    $dir = Split-Path -Parent $TargetPath
    if ($dir -and -not (Test-Path $dir)) {
        New-Item -ItemType Directory -Force -Path $dir | Out-Null
    }
    $utf8NoBom = New-Object System.Text.UTF8Encoding $false
    [System.IO.File]::WriteAllText($TargetPath, ($out -join "`n") + "`n", $utf8NoBom)
    return $true
}

function Merge-TomlConfigFile {
    param(
        [Parameter(Mandatory = $true)][string]$TargetPath,
        [Parameter(Mandatory = $true)][string]$TemplatePath,
        [string]$SourcePath = ""
    )
    $templateTbl = Read-FlatTomlHashtable $TemplatePath
    if ($null -eq $templateTbl) {
        throw "Invalid template TOML: $TemplatePath"
    }
    $userTbl = $null
    if ($SourcePath -and (Test-Path -LiteralPath $SourcePath)) {
        if ($SourcePath -like "*.json") {
            $userTbl = Read-JsonConfigFile $SourcePath
        } else {
            $userTbl = Read-FlatTomlHashtable $SourcePath
        }
    } elseif (Test-Path -LiteralPath $TargetPath) {
        $userTbl = Read-FlatTomlHashtable $TargetPath
    }
    $merged = [ordered]@{}
    foreach ($key in $templateTbl.Keys) {
        if ($null -ne $userTbl -and $userTbl.Contains($key)) {
            $merged[$key] = $userTbl[$key]
        } else {
            $merged[$key] = $templateTbl[$key]
        }
    }
    if ($null -ne $userTbl) {
        foreach ($key in $userTbl.Keys) {
            if (-not $merged.Contains($key)) {
                $merged[$key] = $userTbl[$key]
            }
        }
    }
    Write-MergedTomlFile -TargetPath $TargetPath -TemplatePath $TemplatePath -Merged $merged
    return $true
}

function Import-LegacyConfigIfNeeded {
    param(
        [Parameter(Mandatory = $true)][string]$TargetPath,
        [Parameter(Mandatory = $true)][string]$LegacyPath
    )
    if (Test-Path -LiteralPath $TargetPath) { return $false }
    if (-not (Test-Path -LiteralPath $LegacyPath)) { return $false }
    Copy-Item -LiteralPath $LegacyPath -Destination $TargetPath -Force
    return $true
}

function Initialize-UserTomlConfig {
    <#
    确保用户配置为 TOML：从 ProgramData 旧路径、Roaming JSON 迁移并合并 example 模板，最后删除 JSON。
    #>
    param(
        [Parameter(Mandatory = $true)][string]$TomlPath,
        [Parameter(Mandatory = $true)][string]$JsonPath,
        [Parameter(Mandatory = $true)][string]$TemplatePath,
        [string]$LegacyJsonPath = ""
    )

    if (-not (Test-Path -LiteralPath $TemplatePath)) {
        throw "Template not found: $TemplatePath"
    }

    if ($LegacyJsonPath -and (Test-Path -LiteralPath $LegacyJsonPath)) {
        if (-not (Test-Path -LiteralPath $TomlPath) -and -not (Test-Path -LiteralPath $JsonPath)) {
            Copy-Item -LiteralPath $LegacyJsonPath -Destination $JsonPath -Force
        }
    }

    $hadJson = Test-Path -LiteralPath $JsonPath
    $hadToml = Test-Path -LiteralPath $TomlPath

    if (-not $hadToml -and $hadJson) {
        $jsonObj = Read-JsonConfigFile $JsonPath
        if ($null -eq $jsonObj) {
            Copy-Item -LiteralPath $TemplatePath -Destination $TomlPath -Force
        } else {
            $tbl = [ordered]@{}
            foreach ($prop in $jsonObj.PSObject.Properties) {
                $tbl[$prop.Name] = $prop.Value
            }
            Write-MergedTomlFile -TargetPath $TomlPath -TemplatePath $TemplatePath -Merged $tbl
        }
        Remove-Item -LiteralPath $JsonPath -Force -ErrorAction SilentlyContinue
        Merge-TomlConfigFile -TargetPath $TomlPath -TemplatePath $TemplatePath
        return "migrated_json"
    }

    if ($hadToml -and $hadJson) {
        Merge-TomlConfigFile -TargetPath $TomlPath -TemplatePath $TemplatePath -SourcePath $JsonPath
        Remove-Item -LiteralPath $JsonPath -Force -ErrorAction SilentlyContinue
        return "merged_json_into_toml"
    }

    if ($hadToml) {
        Merge-TomlConfigFile -TargetPath $TomlPath -TemplatePath $TemplatePath
        return "merged_toml"
    }

    Copy-Item -LiteralPath $TemplatePath -Destination $TomlPath -Force
    return "created_new"
}

# 兼容旧脚本：仅 JSON 合并（Bridge/Web 已改用 Initialize-UserTomlConfig）
function Merge-JsonConfigFile {
    param(
        [Parameter(Mandatory = $true)][string]$TargetPath,
        [Parameter(Mandatory = $true)][string]$TemplatePath,
        [string]$SourcePath = ""
    )
    if (-not (Test-Path $TemplatePath)) {
        throw "Template not found: $TemplatePath"
    }
    $templateRaw = Get-Content -LiteralPath $TemplatePath -Raw -Encoding UTF8
    $templateObj = $templateRaw | ConvertFrom-Json
    if ($null -eq $templateObj) {
        throw "Invalid template JSON: $TemplatePath"
    }
    $baseObj = $null
    if ($SourcePath -and (Test-Path -LiteralPath $SourcePath)) {
        $baseObj = Read-JsonConfigFile $SourcePath
    } elseif (Test-Path -LiteralPath $TargetPath) {
        $baseObj = Read-JsonConfigFile $TargetPath
    }
    $merged = [ordered]@{}
    foreach ($prop in $templateObj.PSObject.Properties) {
        if ($null -ne $baseObj -and ($baseObj.PSObject.Properties.Name -contains $prop.Name)) {
            $merged[$prop.Name] = $baseObj.($prop.Name)
        } else {
            $merged[$prop.Name] = $prop.Value
        }
    }
    if ($null -ne $baseObj) {
        foreach ($prop in $baseObj.PSObject.Properties) {
            if (-not $merged.Contains($prop.Name)) {
                $merged[$prop.Name] = $prop.Value
            }
        }
    }
    $dir = Split-Path -Parent $TargetPath
    if ($dir -and -not (Test-Path $dir)) {
        New-Item -ItemType Directory -Force -Path $dir | Out-Null
    }
    $json = $merged | ConvertTo-Json -Depth 20
    $utf8NoBom = New-Object System.Text.UTF8Encoding $false
    [System.IO.File]::WriteAllText($TargetPath, $json + "`n", $utf8NoBom)
    return $true
}
