[CmdletBinding()]
param(
    [switch]$StartBuild
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$CompilerRoot = "C:\Users\eorb9\projects\news_bot_resume_clean_7198b6b"
$ExpectedCommit = "7198b6b74bbd10f1cf2451ca399c0b63f14706a9"
$SourceProject = "C:\Users\eorb9\projects\news_bot\production\staging\P9IMPORT-3D770A7DD72457C97098\project"
$ExpectedManifestSha256 = "6c05dcf49b301997dde3483b97f46668b5fb3f29fc2ea5fe67dc2c3e13fd4576"
$ExpectedSnapshotId = "MEMIDX-1e64a1b6e6ba7b07b799"
$CheckpointDirectory = "C:\Users\eorb9\projects\news_bot\runs\checkpoints\llm"
$CheckpointSentinel = "LLMCKPT-1d6d8295e6996522.json"
$ProtectedRoot = "C:\Users\eorb9\projects\bithumb-quant-trader"
$LogDirectory = "C:\Users\eorb9\projects\news_bot_trash\20260930_nslab_resource_guard\resource_logs"
$QuotaResetUtc = [DateTimeOffset]::Parse("2026-10-03T18:31:00Z")
$AffinityMaskValue = [long]0xF
$SampleIntervalSeconds = 10
$TreeRefreshSeconds = 2
$WarningPrivateBytes = [long](8GB)
$MinimumAvailableBytes = [long](6GB)
$LowMemoryStopSeconds = 60

function Assert-PathEquals {
    param(
        [string]$Actual,
        [string]$Expected,
        [string]$Label
    )

    $actualPath = [IO.Path]::GetFullPath($Actual).TrimEnd("\")
    $expectedPath = [IO.Path]::GetFullPath($Expected).TrimEnd("\")
    if (-not [string]::Equals($actualPath, $expectedPath, [StringComparison]::OrdinalIgnoreCase)) {
        throw "$Label mismatch. Expected '$expectedPath'; found '$actualPath'."
    }
}

function Get-ProcessCreationTime {
    param([object]$ProcessInfo)

    if ($ProcessInfo.CreationDate -is [datetime]) {
        return [datetime]$ProcessInfo.CreationDate
    }
    return [System.Management.ManagementDateTimeConverter]::ToDateTime([string]$ProcessInfo.CreationDate)
}

function Test-IsProtectedProcess {
    param([object]$ProcessInfo)

    $identity = "{0} {1}" -f [string]$ProcessInfo.ExecutablePath, [string]$ProcessInfo.CommandLine
    return $identity.IndexOf($ProtectedRoot, [StringComparison]::OrdinalIgnoreCase) -ge 0
}

function Get-ProcessInfo {
    param([int]$ProcessId)

    return Get-CimInstance -ClassName Win32_Process -Filter "ProcessId = $ProcessId" -ErrorAction SilentlyContinue
}

function Assert-ExpectedBuildProcess {
    param([object]$ProcessInfo)

    if ($null -eq $ProcessInfo -or [string]::IsNullOrWhiteSpace([string]$ProcessInfo.ExecutablePath) -or
        [string]::IsNullOrWhiteSpace([string]$ProcessInfo.CommandLine)) {
        throw "The compiler process identity is unavailable; refusing process control."
    }
    if (Test-IsProtectedProcess $ProcessInfo) {
        throw "The compiler PID resolves under the protected Bithumb root; refusing process control."
    }

    $exe = [IO.Path]::GetFullPath([string]$ProcessInfo.ExecutablePath)
    $expectedCli = [regex]::Escape("news_scalping_lab.cli")
    $command = [string]$ProcessInfo.CommandLine
    if ([IO.Path]::GetFileName($exe) -notmatch "^python(w)?\.exe$" -or
        $command -notmatch $expectedCli -or
        $command -notmatch "brain\s+build-offline" -or
        $command.IndexOf($SourceProject, [StringComparison]::OrdinalIgnoreCase) -lt 0 -or
        $command.IndexOf($ExpectedManifestSha256, [StringComparison]::OrdinalIgnoreCase) -lt 0 -or
        $command.IndexOf($CheckpointDirectory, [StringComparison]::OrdinalIgnoreCase) -lt 0) {
        throw "PID $($ProcessInfo.ProcessId) does not match the pinned build command; refusing process control."
    }
}

function Get-VerifiedBuildTree {
    param(
        [int]$RootProcessId,
        [datetime]$RootCreationTime
    )

    $allProcesses = @(Get-CimInstance -ClassName Win32_Process -ErrorAction Stop)
    $childrenByParent = @{}
    foreach ($processInfo in $allProcesses) {
        $parentId = [int]$processInfo.ParentProcessId
        if (-not $childrenByParent.ContainsKey($parentId)) {
            $childrenByParent[$parentId] = [System.Collections.Generic.List[object]]::new()
        }
        $childrenByParent[$parentId].Add($processInfo)
    }

    $queue = [System.Collections.Generic.Queue[object]]::new()
    $queue.Enqueue([pscustomobject]@{ ProcessId = $RootProcessId; Depth = 0 })
    $seen = [System.Collections.Generic.HashSet[int]]::new()
    [void]$seen.Add($RootProcessId)
    $descendants = [System.Collections.Generic.List[object]]::new()
    $ambiguous = [System.Collections.Generic.List[int]]::new()

    while ($queue.Count -gt 0) {
        $parent = $queue.Dequeue()
        if (-not $childrenByParent.ContainsKey([int]$parent.ProcessId)) {
            continue
        }

        foreach ($child in $childrenByParent[[int]$parent.ProcessId]) {
            $childId = [int]$child.ProcessId
            if ($seen.Contains($childId)) {
                continue
            }

            $created = Get-ProcessCreationTime $child
            if ($created -lt $RootCreationTime) {
                continue
            }
            [void]$seen.Add($childId)
            $queue.Enqueue([pscustomobject]@{ ProcessId = $childId; Depth = ([int]$parent.Depth + 1) })

            if (Test-IsProtectedProcess $child) {
                throw "A compiler descendant resolves under the protected Bithumb root; no process control was applied to it."
            }
            if ([string]::IsNullOrWhiteSpace([string]$child.ExecutablePath) -or
                [string]::IsNullOrWhiteSpace([string]$child.CommandLine)) {
                $ambiguous.Add($childId)
                continue
            }

            $descendants.Add([pscustomobject]@{
                ProcessId = $childId
                ParentProcessId = [int]$child.ParentProcessId
                Depth = ([int]$parent.Depth + 1)
                Name = [string]$child.Name
                ExecutablePath = [string]$child.ExecutablePath
                CommandLine = [string]$child.CommandLine
                CreationDate = $created
            })
        }
    }

    return [pscustomobject]@{
        Descendants = @($descendants)
        AmbiguousProcessIds = @($ambiguous)
    }
}

function Set-VerifiedAffinity {
    param([object]$ProcessInfo)

    $currentInfo = Get-ProcessInfo ([int]$ProcessInfo.ProcessId)
    if ($null -eq $currentInfo) {
        return $null
    }
    if (Test-IsProtectedProcess $currentInfo) {
        throw "Refusing to set affinity for protected process PID $($ProcessInfo.ProcessId)."
    }
    if ([string]::IsNullOrWhiteSpace([string]$currentInfo.ExecutablePath) -or
        [string]::IsNullOrWhiteSpace([string]$currentInfo.CommandLine)) {
        throw "PID $($ProcessInfo.ProcessId) is ambiguous; refusing to set its affinity."
    }
    if ((Get-ProcessCreationTime $currentInfo) -ne (Get-ProcessCreationTime $ProcessInfo) -or
        [string]$currentInfo.ExecutablePath -ne [string]$ProcessInfo.ExecutablePath -or
        [string]$currentInfo.CommandLine -ne [string]$ProcessInfo.CommandLine) {
        throw "PID $($ProcessInfo.ProcessId) identity changed; refusing to set affinity."
    }

    $process = Get-Process -Id ([int]$ProcessInfo.ProcessId) -ErrorAction Stop
    $process.ProcessorAffinity = [IntPtr]$AffinityMaskValue
    $actualMask = [long]$process.ProcessorAffinity.ToInt64()
    if ($actualMask -ne $AffinityMaskValue) {
        throw "PID $($ProcessInfo.ProcessId) affinity readback was 0x$('{0:X}' -f $actualMask), expected 0xF."
    }
    return $actualMask
}

function Get-HostResourceSnapshot {
    $operatingSystem = Get-CimInstance -ClassName Win32_OperatingSystem -ErrorAction Stop
    $availableBytes = [long]$operatingSystem.FreePhysicalMemory * 1KB
    $pagefiles = @(Get-CimInstance -ClassName Win32_PageFileUsage -ErrorAction SilentlyContinue)
    $pagefileCurrentMiB = [long]0
    foreach ($pagefile in $pagefiles) {
        $pagefileCurrentMiB += [long]$pagefile.CurrentUsage
    }
    $drive = Get-PSDrive -Name C -ErrorAction Stop

    return [pscustomobject]@{
        AvailableBytes = $availableBytes
        PagefileCurrentMiB = $pagefileCurrentMiB
        DriveCFreeBytes = [long]$drive.Free
    }
}

function Get-BuildResourceSnapshot {
    param(
        [int]$RootProcessId,
        [object[]]$Descendants,
        [int[]]$AmbiguousProcessIds,
        [object]$HostResources
    )

    $rows = [System.Collections.Generic.List[object]]::new()
    $all = @([pscustomobject]@{ ProcessId = $RootProcessId; Name = "python.exe" }) + @($Descendants)
    foreach ($entry in $all) {
        try {
            $process = Get-Process -Id ([int]$entry.ProcessId) -ErrorAction Stop
            $mask = [long]$process.ProcessorAffinity.ToInt64()
            $rows.Add([pscustomobject]@{
                ProcessId = [int]$process.Id
                Name = [string]$process.ProcessName
                PrivateBytes = [long]$process.PrivateMemorySize64
                WorkingSetBytes = [long]$process.WorkingSet64
                CpuSeconds = [double]$process.CPU
                AffinityMask = [long]$mask
            })
        }
        catch {
            # A child may exit between the process-tree and metric snapshots.
        }
    }

    $rootRow = @($rows | Where-Object ProcessId -eq $RootProcessId | Select-Object -First 1)
    $totalPrivate = [long]0
    $totalWorkingSet = [long]0
    $totalCpuSeconds = [double]0
    foreach ($row in $rows) {
        $totalPrivate += [long]$row.PrivateBytes
        $totalWorkingSet += [long]$row.WorkingSetBytes
        $totalCpuSeconds += [double]$row.CpuSeconds
    }

    return [pscustomobject]@{
        TimestampUtc = [DateTimeOffset]::UtcNow.ToString("o")
        RootProcessId = $RootProcessId
        RootPrivateBytes = if ($rootRow.Count -gt 0) { [long]$rootRow[0].PrivateBytes } else { $null }
        RootWorkingSetBytes = if ($rootRow.Count -gt 0) { [long]$rootRow[0].WorkingSetBytes } else { $null }
        RootCpuSeconds = if ($rootRow.Count -gt 0) { [double]$rootRow[0].CpuSeconds } else { $null }
        RootAffinityMask = if ($rootRow.Count -gt 0) { [long]$rootRow[0].AffinityMask } else { $null }
        BuildTreeProcessCount = $rows.Count
        BuildTreePrivateBytes = $totalPrivate
        BuildTreeWorkingSetBytes = $totalWorkingSet
        BuildTreeCpuSeconds = $totalCpuSeconds
        AmbiguousProcessIds = @($AmbiguousProcessIds)
        ChildAffinities = @($rows | Where-Object ProcessId -ne $RootProcessId | Select-Object ProcessId, Name, AffinityMask)
        AvailableBytes = [long]$HostResources.AvailableBytes
        PagefileCurrentMiB = [long]$HostResources.PagefileCurrentMiB
        DriveCFreeBytes = [long]$HostResources.DriveCFreeBytes
    }
}

function Stop-VerifiedBuildTree {
    param(
        [int]$RootProcessId,
        [datetime]$RootCreationTime
    )

    $rootInfo = Get-ProcessInfo $RootProcessId
    if ($null -eq $rootInfo) {
        return
    }
    Assert-ExpectedBuildProcess $rootInfo
    if ((Get-ProcessCreationTime $rootInfo) -ne $RootCreationTime) {
        throw "The compiler PID was reused; refusing to stop it."
    }

    $tree = Get-VerifiedBuildTree -RootProcessId $RootProcessId -RootCreationTime $RootCreationTime
    if ($tree.AmbiguousProcessIds.Count -gt 0) {
        Write-Warning "Ambiguous descendant PIDs left untouched: $($tree.AmbiguousProcessIds -join ', ')"
    }

    foreach ($child in @($tree.Descendants | Sort-Object Depth -Descending)) {
        $current = Get-ProcessInfo ([int]$child.ProcessId)
        if ($null -eq $current) {
            continue
        }
        if (Test-IsProtectedProcess $current) {
            throw "PID $($child.ProcessId) now resolves under the protected Bithumb root; refusing to stop it."
        }
        if ([string]$current.ExecutablePath -ne [string]$child.ExecutablePath -or
            [string]$current.CommandLine -ne [string]$child.CommandLine -or
            [int]$current.ParentProcessId -ne [int]$child.ParentProcessId -or
            (Get-ProcessCreationTime $current) -ne [datetime]$child.CreationDate) {
            Write-Warning "PID $($child.ProcessId) identity changed; left untouched."
            continue
        }
        Stop-Process -Id ([int]$child.ProcessId) -Force -ErrorAction Stop
    }

    $rootInfo = Get-ProcessInfo $RootProcessId
    if ($null -ne $rootInfo) {
        Assert-ExpectedBuildProcess $rootInfo
        if ((Get-ProcessCreationTime $rootInfo) -ne $RootCreationTime) {
            throw "The compiler PID was reused during stop; refusing to stop it."
        }
        Stop-Process -Id $RootProcessId -Force -ErrorAction Stop
    }
}

function Stop-VerifiedCompilerRoot {
    param(
        [int]$RootProcessId,
        [datetime]$RootCreationTime
    )

    $rootInfo = Get-ProcessInfo $RootProcessId
    if ($null -eq $rootInfo) {
        return
    }
    Assert-ExpectedBuildProcess $rootInfo
    if ((Get-ProcessCreationTime $rootInfo) -ne $RootCreationTime) {
        throw "The compiler PID was reused; refusing to stop it."
    }
    Stop-Process -Id $RootProcessId -Force -ErrorAction Stop
}

function Set-ScopedEnvironment {
    param([hashtable]$Values)

    $saved = @{}
    foreach ($name in $Values.Keys) {
        $saved[$name] = [Environment]::GetEnvironmentVariable($name, "Process")
        [Environment]::SetEnvironmentVariable($name, [string]$Values[$name], "Process")
    }
    return $saved
}

function Restore-ScopedEnvironment {
    param([hashtable]$Values)

    foreach ($name in $Values.Keys) {
        [Environment]::SetEnvironmentVariable($name, $Values[$name], "Process")
    }
}

function Get-Preflight {
    if (-not (Test-Path -LiteralPath $CompilerRoot -PathType Container)) {
        throw "Pinned compiler worktree is missing: $CompilerRoot"
    }
    $actualCommit = (& git -C $CompilerRoot rev-parse HEAD).Trim()
    if ($LASTEXITCODE -ne 0 -or $actualCommit -ne $ExpectedCommit) {
        throw "Pinned compiler commit mismatch: $actualCommit"
    }
    $worktreeStatus = @(& git -C $CompilerRoot status --porcelain --untracked-files=all)
    if ($LASTEXITCODE -ne 0 -or -not [string]::IsNullOrWhiteSpace(($worktreeStatus -join "`n"))) {
        throw "Pinned compiler worktree is not clean."
    }

    $pointerPath = Join-Path $SourceProject "memory\retrieval_index\current.json"
    if (-not (Test-Path -LiteralPath $pointerPath -PathType Leaf)) {
        throw "Source memory pointer is missing: $pointerPath"
    }
    $pointer = Get-Content -LiteralPath $pointerPath -Raw -Encoding UTF8 | ConvertFrom-Json
    if ([string]$pointer.snapshot_id -ne $ExpectedSnapshotId) {
        throw "Source snapshot mismatch: $($pointer.snapshot_id)"
    }
    $manifestPath = [IO.Path]::GetFullPath((Join-Path $SourceProject ([string]$pointer.manifest_path)))
    $sourcePrefix = [IO.Path]::GetFullPath($SourceProject).TrimEnd("\") + "\"
    if (-not $manifestPath.StartsWith($sourcePrefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Source manifest escaped the immutable project root."
    }
    if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) {
        throw "Source manifest is missing: $manifestPath"
    }
    $actualManifestSha256 = (Get-FileHash -LiteralPath $manifestPath -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actualManifestSha256 -ne $ExpectedManifestSha256) {
        throw "Source manifest SHA mismatch: $actualManifestSha256"
    }

    if (-not (Test-Path -LiteralPath $CheckpointDirectory -PathType Container)) {
        throw "Shared checkpoint directory is missing: $CheckpointDirectory"
    }
    $sentinelPath = Join-Path $CheckpointDirectory $CheckpointSentinel
    if (-not (Test-Path -LiteralPath $sentinelPath -PathType Leaf)) {
        throw "Expected quota sentinel is missing: $sentinelPath"
    }
    $checkpointFiles = @(Get-ChildItem -LiteralPath $CheckpointDirectory -File -Filter "*.json")
    if ($checkpointFiles.Count -lt 7000) {
        throw "Checkpoint count is unexpectedly low: $($checkpointFiles.Count)"
    }
    $checkpointBytes = [long](($checkpointFiles | Measure-Object -Property Length -Sum).Sum)

    $pythonCommand = Get-Command python -ErrorAction Stop
    $pythonPath = [IO.Path]::GetFullPath($pythonCommand.Source)
    $expectedCli = [IO.Path]::GetFullPath((Join-Path $CompilerRoot "src\news_scalping_lab\cli.py"))
    $probe = @'
import importlib.util
import json
import sys
from pathlib import Path
from news_scalping_lab.config import load_settings

settings = load_settings()
spec = importlib.util.find_spec('news_scalping_lab.cli')
print(json.dumps({
    'cli': str(Path(spec.origin).resolve()),
    'project_root': str(Path.cwd().resolve()),
    'python': sys.executable,
    'python_version': sys.version.split()[0],
    'provider': settings.llm_provider,
    'model': settings.codex_model,
    'reasoning_effort': settings.codex_reasoning_effort,
    'max_concurrency': settings.limits.max_concurrency,
    'codex_command': settings.codex_command,
}))
'@
    $probeEnvironment = Set-ScopedEnvironment @{
        PYTHONPATH = (Join-Path $CompilerRoot "src")
        NSLAB_LLM_PROVIDER = "codex-oauth"
        NSLAB_CODEX_MODEL = "gpt-5.6-sol"
        NSLAB_CODEX_REASONING_EFFORT = "xhigh"
        NSLAB_MAX_CONCURRENCY = "4"
    }
    $originalLocation = Get-Location
    try {
        Set-Location -LiteralPath $CompilerRoot
        $probeOutput = @(& $pythonPath -c $probe)
        if ($LASTEXITCODE -ne 0 -or $probeOutput.Count -eq 0) {
            throw "Pinned compiler config probe failed."
        }
        $settings = ($probeOutput | Select-Object -Last 1) | ConvertFrom-Json
    }
    finally {
        Set-Location -LiteralPath $originalLocation
        Restore-ScopedEnvironment $probeEnvironment
    }
    Assert-PathEquals ([string]$settings.cli) $expectedCli "CLI import"
    Assert-PathEquals ([string]$settings.project_root) $CompilerRoot "Settings project root"
    Assert-PathEquals ([string]$settings.python) $pythonPath "Python executable"
    if ([string]$settings.provider -ne "codex-oauth" -or
        [string]$settings.model -ne "gpt-5.6-sol" -or
        [string]$settings.reasoning_effort -ne "xhigh" -or
        [int]$settings.max_concurrency -ne 4) {
        throw "Effective provider/model/reasoning/concurrency does not match the pinned build identity."
    }

    $buildProcesses = @(
        Get-CimInstance -ClassName Win32_Process -Filter "Name = 'python.exe' OR Name = 'pythonw.exe'" |
            Where-Object {
                $command = [string]$_.CommandLine
                $command -match "news_scalping_lab\.cli.*brain\s+build-offline" -and
                ($command.IndexOf($SourceProject, [StringComparison]::OrdinalIgnoreCase) -ge 0 -or
                 $command.IndexOf($CheckpointDirectory, [StringComparison]::OrdinalIgnoreCase) -ge 0)
            }
    )
    if ($buildProcesses.Count -gt 0) {
        throw "A build using the pinned source/checkpoints is already running (PID $($buildProcesses.ProcessId -join ', '))."
    }

    $hostResources = Get-HostResourceSnapshot
    $computer = Get-CimInstance -ClassName Win32_ComputerSystem
    if ([int]$computer.NumberOfLogicalProcessors -lt 4) {
        throw "The host has fewer than four logical processors; affinity mask 0xF cannot be applied."
    }

    return [pscustomobject]@{
        CompilerCommit = $actualCommit
        CompilerRoot = $CompilerRoot
        Python = $pythonPath
        PythonVersion = [string]$settings.python_version
        Cli = [string]$settings.cli
        Provider = [string]$settings.provider
        Model = [string]$settings.model
        ReasoningEffort = [string]$settings.reasoning_effort
        MaxConcurrency = [int]$settings.max_concurrency
        CodexCommand = [string]$settings.codex_command
        SourceSnapshotId = [string]$pointer.snapshot_id
        ManifestSha256 = $actualManifestSha256
        PointerManifestSha256 = [string]$pointer.manifest_sha256
        CheckpointCount = $checkpointFiles.Count
        CheckpointBytes = $checkpointBytes
        HostResources = $hostResources
        LogicalProcessors = [int]$computer.NumberOfLogicalProcessors
    }
}

$preflight = Get-Preflight
Write-Host "Preflight: PASS (no LLM/OAuth call made)"
Write-Host ("Compiler: {0} @ {1}" -f $preflight.CompilerCommit, $preflight.CompilerRoot)
Write-Host ("Python: {0} ({1}); CLI: {2}" -f $preflight.Python, $preflight.PythonVersion, $preflight.Cli)
Write-Host ("Identity: {0}/{1}/{2}; max_concurrency={3}; Codex command={4}" -f $preflight.Provider, $preflight.Model, $preflight.ReasoningEffort, $preflight.MaxConcurrency, $preflight.CodexCommand)
Write-Host ("Source: {0}; manifest SHA-256={1}; pointer's legacy SHA-256={2}" -f $preflight.SourceSnapshotId, $preflight.ManifestSha256, $preflight.PointerManifestSha256)
Write-Host ("Checkpoints: {0} JSON files, {1:N0} bytes; quota sentinel present" -f $preflight.CheckpointCount, $preflight.CheckpointBytes)
Write-Host ("Host: {0} logical processors; available RAM {1:N2} GiB; pagefile use {2:N0} MiB; C: free {3:N2} GiB" -f $preflight.LogicalProcessors, ($preflight.HostResources.AvailableBytes / 1GB), $preflight.HostResources.PagefileCurrentMiB, ($preflight.HostResources.DriveCFreeBytes / 1GB))
Write-Host ("OAuth reset: {0} KST ({1} UTC)" -f $QuotaResetUtc.ToOffset([TimeSpan]::FromHours(9)).ToString("yyyy-MM-dd HH:mm:ss"), $QuotaResetUtc.ToString("yyyy-MM-dd HH:mm:ss"))

if (-not $StartBuild) {
    Write-Host "Preflight-only mode. No build process was started. Pass -StartBuild to request the guarded resume."
    return
}

if ([DateTimeOffset]::UtcNow -lt $QuotaResetUtc) {
    throw "Quota reset has not passed; build was not started."
}
if ([long]$preflight.HostResources.AvailableBytes -lt $MinimumAvailableBytes) {
    throw "Available RAM is below 6 GiB; build was not started."
}

New-Item -ItemType Directory -Path $LogDirectory -Force | Out-Null
$logPath = Join-Path $LogDirectory ("offline_v5_{0}.jsonl" -f [DateTimeOffset]::UtcNow.ToString("yyyyMMddTHHmmssZ"))
$buildArguments = "-m news_scalping_lab.cli brain build-offline --source-project `"$SourceProject`" --expected-manifest-sha256 $ExpectedManifestSha256 --checkpoint-dir `"$CheckpointDirectory`""
$buildEnvironment = Set-ScopedEnvironment @{
    PYTHONPATH = (Join-Path $CompilerRoot "src")
    NSLAB_LLM_PROVIDER = "codex-oauth"
    NSLAB_CODEX_MODEL = "gpt-5.6-sol"
    NSLAB_CODEX_REASONING_EFFORT = "xhigh"
    NSLAB_MAX_CONCURRENCY = "4"
}
try {
    $build = Start-Process -FilePath $preflight.Python -ArgumentList $buildArguments -WorkingDirectory $CompilerRoot -PassThru -NoNewWindow
}
finally {
    Restore-ScopedEnvironment $buildEnvironment
}

$rootCreationTime = $null
try {
    $rootInfo = $null
    for ($attempt = 0; $attempt -lt 100 -and $null -eq $rootInfo; $attempt++) {
        $build.Refresh()
        if ($build.HasExited) {
            throw "Compiler exited during startup with code $($build.ExitCode); no resource guard was attached."
        }
        $rootInfo = Get-ProcessInfo $build.Id
        if ($null -eq $rootInfo) {
            Start-Sleep -Milliseconds 50
        }
    }
    Assert-ExpectedBuildProcess $rootInfo
    $rootCreationTime = Get-ProcessCreationTime $rootInfo
    $rootAffinity = Set-VerifiedAffinity $rootInfo
    if ($null -eq $rootAffinity) {
        throw "Compiler exited before affinity could be applied."
    }
}
catch {
    if ($null -ne $rootCreationTime) {
        try {
            Stop-VerifiedCompilerRoot -RootProcessId $build.Id -RootCreationTime $rootCreationTime
        }
        catch {
            Write-Warning "Startup guard failed and the exact compiler root could not be stopped: $($_.Exception.Message)"
        }
    }
    throw
}
Write-Host ("Started guarded build PID {0}; affinity readback 0x{1:X}; log {2}" -f $build.Id, $rootAffinity, $logPath)

$lowMemorySince = $null
$previousPrivateBytes = $null
$privateGrowthSamples = 0
$growthWarningWritten = $false
$highPrivateWarningWritten = $false
$nextSample = [DateTimeOffset]::UtcNow
$stoppedForGuard = $false

try {
    while ($true) {
        $build.Refresh()
        if ($build.HasExited) {
            break
        }

        $currentRoot = Get-ProcessInfo $build.Id
        if ($null -eq $currentRoot) {
            $build.Refresh()
            if ($build.HasExited) {
                break
            }
            throw "Compiler PID $($build.Id) disappeared before its exit status could be read."
        }
        Assert-ExpectedBuildProcess $currentRoot
        if ((Get-ProcessCreationTime $currentRoot) -ne $rootCreationTime) {
            throw "Compiler PID identity changed; resource control stopped."
        }
        $rootAffinity = Set-VerifiedAffinity $currentRoot
        if ($null -eq $rootAffinity) {
            $build.Refresh()
            if ($build.HasExited) {
                break
            }
            throw "Compiler exited before the resource guard could refresh its affinity."
        }
        $tree = Get-VerifiedBuildTree -RootProcessId $build.Id -RootCreationTime $rootCreationTime
        foreach ($child in $tree.Descendants) {
            [void](Set-VerifiedAffinity $child)
        }
        if ($tree.AmbiguousProcessIds.Count -gt 0) {
            Write-Warning ("Descendants without resolvable executable/command line left untouched: {0}" -f ($tree.AmbiguousProcessIds -join ", "))
        }

        $now = [DateTimeOffset]::UtcNow
        if ($now -ge $nextSample) {
            $hostResources = Get-HostResourceSnapshot
            $sample = Get-BuildResourceSnapshot -RootProcessId $build.Id -Descendants $tree.Descendants -AmbiguousProcessIds $tree.AmbiguousProcessIds -HostResources $hostResources
            $jsonLine = $sample | ConvertTo-Json -Depth 6 -Compress
            Add-Content -LiteralPath $logPath -Value $jsonLine -Encoding UTF8
            Write-Host ("{0} PID {1}: private {2:N2} GiB (tree {3:N2}), working set {4:N2} GiB, CPU {5:N1}s, RAM available {6:N2} GiB, pagefile {7:N0} MiB, C: free {8:N2} GiB" -f $sample.TimestampUtc, $build.Id, ($sample.RootPrivateBytes / 1GB), ($sample.BuildTreePrivateBytes / 1GB), ($sample.RootWorkingSetBytes / 1GB), $sample.RootCpuSeconds, ($sample.AvailableBytes / 1GB), $sample.PagefileCurrentMiB, ($sample.DriveCFreeBytes / 1GB))

            if ([long]$sample.BuildTreePrivateBytes -ge $WarningPrivateBytes -and -not $highPrivateWarningWritten) {
                Write-Warning "Build process tree private bytes reached 8 GiB; inspect the logged trend (this is a warning, not itself a leak diagnosis)."
                $highPrivateWarningWritten = $true
            }
            elseif ([long]$sample.BuildTreePrivateBytes -lt $WarningPrivateBytes) {
                $highPrivateWarningWritten = $false
            }
            if ($null -ne $previousPrivateBytes -and
                [long]$sample.BuildTreePrivateBytes -ge $WarningPrivateBytes -and
                [long]$sample.BuildTreePrivateBytes -gt [long]$previousPrivateBytes) {
                $privateGrowthSamples++
            }
            else {
                $privateGrowthSamples = 0
                $growthWarningWritten = $false
            }
            if ($privateGrowthSamples -ge 6 -and -not $growthWarningWritten) {
                Write-Warning "Build process tree private bytes rose across six consecutive samples above 8 GiB; this is a sustained-growth signal, not proof of a leak."
                $growthWarningWritten = $true
            }
            $previousPrivateBytes = [long]$sample.BuildTreePrivateBytes

            if ([long]$sample.AvailableBytes -lt $MinimumAvailableBytes) {
                if ($null -eq $lowMemorySince) {
                    $lowMemorySince = $now
                }
                elseif (($now - $lowMemorySince).TotalSeconds -ge $LowMemoryStopSeconds) {
                    Write-Warning "Available RAM stayed below 6 GiB for 60 seconds; stopping only the verified compiler process tree. Checkpoints will be preserved."
                    Stop-VerifiedBuildTree -RootProcessId $build.Id -RootCreationTime $rootCreationTime
                    $stoppedForGuard = $true
                    break
                }
            }
            else {
                $lowMemorySince = $null
            }
            $nextSample = $now.AddSeconds($SampleIntervalSeconds)
        }

        Start-Sleep -Seconds $TreeRefreshSeconds
    }
}
catch {
    $monitorError = $_
    try {
        Stop-VerifiedCompilerRoot -RootProcessId $build.Id -RootCreationTime $rootCreationTime
    }
    catch {
        Write-Warning "Guard monitor failed and the exact compiler root could not be stopped: $($_.Exception.Message)"
    }
    throw $monitorError
}

$build.Refresh()
if ($stoppedForGuard) {
    Write-Host "Guard stop complete. Shared checkpoints were not deleted. Review the JSONL resource log and compile workdir before resuming."
    exit 2
}
Write-Host ("Build process exited with code {0}. Resource log: {1}" -f $build.ExitCode, $logPath)
if ($build.ExitCode -ne 0) {
    exit $build.ExitCode
}
