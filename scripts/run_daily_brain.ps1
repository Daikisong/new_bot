[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [Parameter(Mandatory = $true)]
    [string]$NewsCsv,

    [string]$TradeDate,

    [string]$Cutoff,

    [string]$ProjectRoot
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$newsPath = (Resolve-Path -LiteralPath $NewsCsv).Path
$newsFile = Get-Item -LiteralPath $newsPath
if ($newsFile.Extension -ne ".csv") {
    throw "NewsCsv must be a .csv file: $newsPath"
}

if (-not $TradeDate) {
    if ($newsFile.BaseName -notmatch "^news_(\d{8})$") {
        throw "TradeDate is required unless the filename is news_YYYYMMDD.csv."
    }
    $rawDate = $Matches[1]
    $TradeDate = "{0}-{1}-{2}" -f $rawDate.Substring(0, 4), $rawDate.Substring(4, 2), $rawDate.Substring(6, 2)
}
try {
    $parsedDate = [DateTime]::ParseExact(
        $TradeDate,
        "yyyy-MM-dd",
        [Globalization.CultureInfo]::InvariantCulture
    )
} catch {
    throw "TradeDate must use YYYY-MM-DD: $TradeDate"
}
if (-not $Cutoff) {
    $Cutoff = "{0}T08:59:59+09:00" -f $parsedDate.ToString("yyyy-MM-dd")
}

if (-not $ProjectRoot) {
    $ProjectRoot = $env:NSLAB_DAILY_PROJECT_ROOT
}
if (-not $ProjectRoot) {
    $ProjectRoot = Join-Path $repoRoot "runs\daily_csv_oot_replay_20261008\project"
}
$projectPath = (Resolve-Path -LiteralPath $ProjectRoot).Path
$pointerPath = Join-Path $projectPath "brain\current\brain_package_pointer.json"
if (-not (Test-Path -LiteralPath $pointerPath -PathType Leaf)) {
    throw "No selected BrainPackage pointer in project: $pointerPath"
}

$pointer = Get-Content -Raw -Encoding UTF8 $pointerPath | ConvertFrom-Json
if ($pointer.production_activated -eq $true) {
    throw "This runner is restricted to isolated research/paper runs and refuses an activated production pointer."
}
$packagePath = [IO.Path]::GetFullPath((Join-Path $projectPath $pointer.package_path))
$projectPrefix = [IO.Path]::GetFullPath($projectPath).TrimEnd([IO.Path]::DirectorySeparatorChar) + [IO.Path]::DirectorySeparatorChar
if (-not $packagePath.StartsWith($projectPrefix, [StringComparison]::OrdinalIgnoreCase)) {
    throw "Selected package path escapes the data project: $packagePath"
}
$packageManifestPath = Join-Path $packagePath "brain_package_manifest.json"
if (-not (Test-Path -LiteralPath $packageManifestPath -PathType Leaf)) {
    throw "Selected BrainPackage manifest is missing: $packageManifestPath"
}
$packageManifest = Get-Content -Raw -Encoding UTF8 $packageManifestPath | ConvertFrom-Json
if ($packageManifest.package_root -ne $pointer.package_root) {
    throw "Selected BrainPackage root does not match its pointer."
}

$predictionPath = Join-Path $projectPath "predictions\$TradeDate.json"
$reportPath = Join-Path $projectPath "reports\${TradeDate}_preopen.md"
if ((Test-Path -LiteralPath $predictionPath) -or (Test-Path -LiteralPath $reportPath)) {
    throw "A canonical daily result already exists for $TradeDate. Preserve it; use another isolated project rather than overwriting or repeating a sealed date."
}

$mode = if ($packageManifest.production_eligible -eq $true) { "paper run; production still not activated" } else { "RESEARCH-ONLY; predictive quality is not approved" }
$action = "Analyze $TradeDate with brain root $($pointer.package_root) ($mode)"
if ($PSCmdlet.ShouldProcess($newsPath, $action)) {
    $previousPythonPath = $env:PYTHONPATH
    try {
        $srcPath = Join-Path $repoRoot "src"
        if ($previousPythonPath) {
            $env:PYTHONPATH = "$srcPath;$previousPythonPath"
        } else {
            $env:PYTHONPATH = $srcPath
        }
        Push-Location $repoRoot
        try {
            $arguments = @(
                "-m", "news_scalping_lab.cli", "analyze-daily",
                "--project-root", $projectPath,
                "--news", $newsPath,
                "--trade-date", $parsedDate.ToString("yyyy-MM-dd"),
                "--cutoff", $Cutoff
            )
            & python @arguments
            if ($LASTEXITCODE -ne 0) {
                throw "analyze-daily failed with exit code $LASTEXITCODE"
            }
        } finally {
            Pop-Location
        }
    } finally {
        $env:PYTHONPATH = $previousPythonPath
    }
    Write-Host "Prediction: $predictionPath"
    Write-Host "Pre-open report: $reportPath"
    Write-Host "Run mode: $mode"
}
