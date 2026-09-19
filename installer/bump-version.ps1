# Bump the installer release version (MAJOR.MINOR.PATCH).
# Usage:
#   .\bump-version.ps1 -Bump patch   # bug fix        0.1.17 -> 0.1.18
#   .\bump-version.ps1 -Bump minor   # Bridge capability  0.1.17 -> 0.2.0
#   .\bump-version.ps1 -Bump major   # architecture / UI  0.1.17 -> 1.0.0
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("major", "minor", "patch")]
    [string]$Bump
)

$ErrorActionPreference = "Stop"
$Utf8NoBom = [System.Text.UTF8Encoding]::new($false)
$Root = Split-Path -Parent $PSScriptRoot
$VersionPath = Join-Path $Root "VERSION"

function Read-Utf8([string]$Path) {
    return [System.IO.File]::ReadAllText($Path)
}

function Write-Utf8([string]$Path, [string]$Text) {
    [System.IO.File]::WriteAllText($Path, $Text, $Utf8NoBom)
}

function Replace-Exact([string]$Path, [string]$Old, [string]$New, [string]$Context) {
    $text = Read-Utf8 $Path
    $idx = $text.IndexOf($Old)
    if ($idx -lt 0) {
        throw "bump-version: pattern not found in ${Path}: $Context"
    }
    $count = [regex]::Matches($text, [regex]::Escape($Old)).Count
    if ($count -ne 1) {
        throw "bump-version: expected 1 occurrence of '$Old' in ${Path} ($Context), found $count"
    }
    Write-Utf8 $Path ($text.Replace($Old, $New))
}

$raw = (Get-Content -LiteralPath $VersionPath -Raw).Trim()
if ($raw -notmatch '^(\d+)\.(\d+)\.(\d+)$') {
    throw "VERSION must be MAJOR.MINOR.PATCH (got '$raw')"
}
$major = [int]$Matches[1]
$minor = [int]$Matches[2]
$patch = [int]$Matches[3]
$old = "$major.$minor.$patch"

switch ($Bump) {
    "major" { $major++; $minor = 0; $patch = 0 }
    "minor" { $minor++; $patch = 0 }
    "patch" { $patch++ }
}
$new = "$major.$minor.$patch"

Write-Host "[bump] $old -> $new  ($Bump)"

Write-Utf8 $VersionPath "$new`n"

Replace-Exact (Join-Path $Root "CornerstoneBridge\pyproject.toml") `
    "version = `"$old`"" "version = `"$new`"" "cornerstone-bridge project version"
Replace-Exact (Join-Path $Root "CornerstoneBridge\src\cornerstone_bridge\__init__.py") `
    "__version__ = `"$old`"" "__version__ = `"$new`"" "cornerstone-bridge __version__"
Replace-Exact (Join-Path $Root "CornerstoneCLI\pyproject.toml") `
    "version = `"$old`"" "version = `"$new`"" "cornerstone-cli project version"
Replace-Exact (Join-Path $Root "CornerstoneWeb\pyproject.toml") `
    "version = `"$old`"" "version = `"$new`"" "cornerstone-web project version"
Replace-Exact (Join-Path $Root "README.md") `
    "**当前版本：$old**" "**当前版本：$new**" "README current version"
Replace-Exact (Join-Path $Root "installer\README.md") `
    "当前 **$old**" "当前 **$new**" "installer README current version"
Replace-Exact (Join-Path $Root "installer\Cornerstone.iss") `
    "#define MyAppVersion `"$old`"" "#define MyAppVersion `"$new`"" "Inno Setup fallback version"

$reason = switch ($Bump) {
    "major" { "架构或界面大更新" }
    "minor" { "增加 Bridge 能力" }
    "patch" { "Bug 修正" }
}
$changelogPath = Join-Path $Root "CHANGELOG.md"
$changelog = Read-Utf8 $changelogPath
$marker = "## [Unreleased]`r`n`r`n---`r`n`r`n"
$markerLf = "## [Unreleased]`n`n---`n`n"
$section = @"
## $new

**定位**：$reason（请补充具体变更）。升版原因：``-Bump $Bump``。

| 包 | 版本 |
| --- | --- |
| cornerstone-bridge | $new |
| cornerstone-web | $new |
| cornerstone-cli | $new |

---

"@
if ($changelog.Contains($marker)) {
    $changelog = $changelog.Replace($marker, $marker + $section)
} elseif ($changelog.Contains($markerLf)) {
    $nl = "`n"
    $sectionLf = $section.Replace("`r`n", $nl)
    $changelog = $changelog.Replace($markerLf, $markerLf + $sectionLf)
} else {
    throw "bump-version: could not find Unreleased marker in CHANGELOG.md"
}
Write-Utf8 $changelogPath $changelog

Write-Host "[bump] updated VERSION, Python packages, README, installer README, Cornerstone.iss, CHANGELOG"
Write-Host "[bump] next: fill in CHANGELOG $new, then .\build-release.ps1"
