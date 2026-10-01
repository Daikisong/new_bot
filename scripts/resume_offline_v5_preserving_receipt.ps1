[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$CompilerRoot = 'C:\Users\eorb9\projects\news_bot_resume_clean_7198b6b'
$ExpectedCommit = '7198b6b74bbd10f1cf2451ca399c0b63f14706a9'
$SourceProject = 'C:\Users\eorb9\projects\news_bot\production\staging\P9IMPORT-3D770A7DD72457C97098\project'
$ExpectedManifestSha256 = '6c05dcf49b301997dde3483b97f46668b5fb3f29fc2ea5fe67dc2c3e13fd4576'
$ExpectedSnapshotId = 'MEMIDX-1e64a1b6e6ba7b07b799'
$CheckpointDirectory = 'C:\Users\eorb9\projects\news_bot\runs\checkpoints\llm'
$ProtectedRoot = 'C:\Users\eorb9\projects\bithumb-quant-trader'
$AffinityMaskValue = [long]0xF

# Reuse identity-checked resource helpers, without invoking any receipt lifecycle code.
$parseTokens = $null
$parseErrors = $null
$helperPath = Join-Path $PSScriptRoot 'guarded_offline_v5_resume.ps1'
$ast = [System.Management.Automation.Language.Parser]::ParseFile($helperPath, [ref]$parseTokens, [ref]$parseErrors)
if ($parseErrors.Count) { throw 'Resource helper script has parse errors.' }
$helperNames = @('Assert-PathEquals', 'Get-ProcessCreationTime', 'Test-IsProtectedProcess',
    'Get-ProcessInfo', 'Assert-ExpectedBuildProcess', 'Get-VerifiedBuildTree',
    'Set-VerifiedAffinity', 'Get-HostResourceSnapshot', 'Get-CompileProgress',
    'Get-BuildResourceSnapshot', 'Stop-VerifiedBuildTree', 'Set-ScopedEnvironment',
    'Restore-ScopedEnvironment')
$definitions = $ast.FindAll({ param($node)
    $node -is [System.Management.Automation.Language.FunctionDefinitionAst]
}, $false)
foreach ($name in $helperNames) {
    $definition = @($definitions | Where-Object Name -eq $name)
    if ($definition.Count -ne 1) { throw "Resource helper missing or ambiguous: $name" }
    . ([scriptblock]::Create($definition[0].Extent.Text))
}

$mutex = [Threading.Mutex]::new($false, 'Local\NSLAB_Offline_V5_7198b6b')
$owned = $false
$build = $null
$rootCreationTime = $null
try {
    try { $owned = $mutex.WaitOne(0) } catch [Threading.AbandonedMutexException] { $owned = $true }
    if (-not $owned) { throw 'Another preserving-receipt V5 launcher is active.' }
    $commit = (& git -C $CompilerRoot rev-parse HEAD).Trim()
    if ($LASTEXITCODE -ne 0 -or $commit -ne $ExpectedCommit) { throw "Compiler pin mismatch: $commit" }
    $dirty = @(& git -C $CompilerRoot status --porcelain --untracked-files=all)
    if ($LASTEXITCODE -ne 0 -or $dirty.Count) { throw 'Pinned compiler worktree is not clean.' }
    $active = @(Get-CimInstance Win32_Process -Filter "Name = 'python.exe' OR Name = 'pythonw.exe'" |
        Where-Object { $_.CommandLine -match 'news_scalping_lab\.cli.*brain\s+build-offline' })
    if ($active.Count) { throw "An offline compiler is already running: $($active.ProcessId -join ', ')" }
    $pointer = Get-Content -Raw -Encoding UTF8 (Join-Path $SourceProject 'memory\retrieval_index\current.json') | ConvertFrom-Json
    if ($pointer.snapshot_id -ne $ExpectedSnapshotId) { throw 'Source snapshot pin mismatch.' }
    $manifest = [IO.Path]::GetFullPath((Join-Path $SourceProject $pointer.manifest_path))
    if (-not $manifest.StartsWith($SourceProject + '\', [StringComparison]::OrdinalIgnoreCase)) { throw 'Source manifest escaped its root.' }
    if ((Get-FileHash -Algorithm SHA256 -LiteralPath $manifest).Hash.ToLowerInvariant() -ne $ExpectedManifestSha256) { throw 'Source manifest hash mismatch.' }
    if (-not (Test-Path -LiteralPath (Join-Path $CheckpointDirectory 'LLMCKPT-1d6d8295e6996522.json'))) { throw 'Shared checkpoint sentinel is missing.' }
    $pythonPath = (Get-Command python -ErrorAction Stop).Source
    $logRoot = 'C:\Users\eorb9\projects\news_bot\runs\resource_logs'
    New-Item -ItemType Directory -Path $logRoot -Force | Out-Null
    $runPrefix = Join-Path $logRoot ('v5_preserved_' + [DateTimeOffset]::UtcNow.ToString('yyyyMMddTHHmmssZ'))
    $environment = Set-ScopedEnvironment @{
        PYTHONPATH = (Join-Path $CompilerRoot 'src')
        NSLAB_LLM_PROVIDER = 'codex-oauth'
        NSLAB_CODEX_MODEL = 'gpt-5.6-sol'
        NSLAB_CODEX_REASONING_EFFORT = 'xhigh'
        NSLAB_MAX_CONCURRENCY = '4'
    }
    try {
        $arguments = "-m news_scalping_lab.cli brain build-offline --source-project `"$SourceProject`" --expected-manifest-sha256 $ExpectedManifestSha256 --checkpoint-dir `"$CheckpointDirectory`""
        $build = Start-Process -FilePath $pythonPath -ArgumentList $arguments -WorkingDirectory $CompilerRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput ($runPrefix + '.stdout.log') -RedirectStandardError ($runPrefix + '.stderr.log')
    } finally { Restore-ScopedEnvironment $environment }
    for ($attempt = 0; $attempt -lt 100; $attempt++) {
        $info = Get-ProcessInfo $build.Id
        if ($null -ne $info) { break }
        $build.Refresh()
        if ($build.HasExited) { throw "Compiler exited during startup: $($build.ExitCode)" }
        Start-Sleep -Milliseconds 50
    }
    Assert-ExpectedBuildProcess $info
    Assert-PathEquals $info.ExecutablePath $pythonPath 'Python executable'
    $rootCreationTime = Get-ProcessCreationTime $info
    if ($null -eq (Set-VerifiedAffinity $info)) { throw 'Root affinity could not be verified.' }
    [pscustomobject]@{ state = 'started'; root_pid = $build.Id; launcher_pid = $PID;
        root_creation_time_utc = $rootCreationTime.ToUniversalTime().ToString('o'); compiler_commit = $ExpectedCommit;
        manifest_sha256 = $ExpectedManifestSha256; checkpoint_directory = $CheckpointDirectory;
        receipt_preserved = $true; stdout = $runPrefix + '.stdout.log'; stderr = $runPrefix + '.stderr.log'
    } | ConvertTo-Json | Set-Content -Encoding UTF8 -LiteralPath ($runPrefix + '.run.json')
    Write-Output "STARTED PID=$($build.Id) RUN=$runPrefix"
    $nextSample = [DateTimeOffset]::UtcNow
    $lowMemorySince = $null
    while ($true) {
        $build.Refresh()
        if ($build.HasExited) { break }
        $info = Get-ProcessInfo $build.Id
        if ($null -eq $info) { Start-Sleep -Milliseconds 200; continue }
        Assert-ExpectedBuildProcess $info
        if ((Get-ProcessCreationTime $info) -ne $rootCreationTime) { throw 'Compiler PID identity changed.' }
        if ($null -eq (Set-VerifiedAffinity $info)) { throw 'Root affinity could not be reverified.' }
        $tree = Get-VerifiedBuildTree $build.Id $rootCreationTime
        foreach ($child in $tree.Descendants) { [void](Set-VerifiedAffinity $child) }
        $now = [DateTimeOffset]::UtcNow
        if ($now -ge $nextSample) {
            $hostResources = Get-HostResourceSnapshot
            $progress = Get-CompileProgress $rootCreationTime
            $sample = Get-BuildResourceSnapshot $build.Id $tree.Descendants $tree.AmbiguousProcessIds $progress $hostResources
            $sample | ConvertTo-Json -Depth 6 -Compress | Add-Content -Encoding UTF8 -LiteralPath ($runPrefix + '.resources.jsonl')
            if ($sample.AvailableBytes -lt 6GB) {
                if ($null -eq $lowMemorySince) { $lowMemorySince = $now }
                elseif (($now - $lowMemorySince).TotalSeconds -ge 60) { throw 'Available RAM stayed below 6 GiB for 60 seconds.' }
            } else { $lowMemorySince = $null }
            $nextSample = $now.AddSeconds(10)
        }
        Start-Sleep -Seconds 2
    }
    [pscustomobject]@{ state = 'exited'; exit_code = $build.ExitCode; root_pid = $build.Id;
        timestamp_utc = [DateTimeOffset]::UtcNow.ToString('o')
    } | ConvertTo-Json | Set-Content -Encoding UTF8 -LiteralPath ($runPrefix + '.exit.json')
    exit $build.ExitCode
} catch {
    if ($null -ne $build -and $null -ne $rootCreationTime) {
        Stop-VerifiedBuildTree -RootProcessId $build.Id -RootCreationTime $rootCreationTime
    }
    throw
} finally {
    if ($owned) { $mutex.ReleaseMutex() }
    $mutex.Dispose()
}
