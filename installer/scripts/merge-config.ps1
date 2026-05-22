# 合并 JSON 配置：已有键保留用户值，模板中新增键补全，已有文件中多出的键一并保留。
function Merge-JsonConfigFile {
    param(
        [Parameter(Mandatory = $true)]
        [string]$TargetPath,
        [Parameter(Mandatory = $true)]
        [string]$TemplatePath,
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
        $baseObj = Get-Content -LiteralPath $SourcePath -Raw -Encoding UTF8 | ConvertFrom-Json
    } elseif (Test-Path -LiteralPath $TargetPath) {
        $baseObj = Get-Content -LiteralPath $TargetPath -Raw -Encoding UTF8 | ConvertFrom-Json
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

function Import-LegacyConfigIfNeeded {
    param(
        [Parameter(Mandatory = $true)]
        [string]$TargetPath,
        [Parameter(Mandatory = $true)]
        [string]$LegacyPath
    )
    if (Test-Path -LiteralPath $TargetPath) { return $false }
    if (-not (Test-Path -LiteralPath $LegacyPath)) { return $false }
    Copy-Item -LiteralPath $LegacyPath -Destination $TargetPath -Force
    return $true
}
