#requires -Version 7.0
<#
.SYNOPSIS
    Commit and push the current changes with a GitHub PAT, optionally opening a
    pull request and arming auto-merge.

.DESCRIPTION
    Automates the parts that are easy to get wrong on this repo:
      * stages tracked changes only by default (never sweeps generated data/,
        docs/ or the Windows case-collision bundle files)
      * refuses to push to the protected base branch
      * blocks staged secrets (tokens, keys, .env files)
      * builds a conventional-commit message from -Type/-Scope/-Description
      * authenticates the push with GITHUB_TOKEN (env or .env) without persisting it
      * on -Pr: creates (or reuses) the PR, arms auto-merge, waits for checks
        and prints failed workflow logs when something breaks

.PARAMETER Type
    Conventional commit type. Required unless -Message is supplied.

.PARAMETER Scope
    Optional commit scope, e.g. pipeline, ui, data.

.PARAMETER Description
    Short imperative description used to build the subject line.

.PARAMETER Message
    Full commit message; bypasses -Type/-Scope/-Description. Use -Body for the
    paragraphs after the subject.

.PARAMETER Body
    Commit/PR body text.

.PARAMETER Files
    Explicit paths to stage. Other staged paths are cleared first.

.PARAMETER Exclude
    Extra path prefixes to exclude from staging.

.PARAMETER Branch
    Target branch. Switches to it, creating it when missing.

.PARAMETER Base
    Base branch for PR creation. Default: main.

.PARAMETER IncludeUntracked
    Also stage untracked files (still honouring excludes).

.PARAMETER IncludeData
    Also stage data/bundles changes (off by default; the hourly pipeline owns them).

.PARAMETER Pr
    Create/reuse a PR for the branch and arm auto-merge.

.PARAMETER NoAutoMerge
    With -Pr: open the PR but leave merging manual.

.PARAMETER WaitSeconds
    With -Pr: poll until the PR merges or closes (0 = do not wait).

.PARAMETER FetchErrors
    Print failed workflow logs and PR check status. Implied by -Pr.

.PARAMETER Rebase
    Rebase onto origin/<Base> when the branch is behind.

.PARAMETER Verify
    Run the repository checks (pytest, npm run build) before committing.

.PARAMETER SkipTests
    Skip the pytest pre-commit check.

.PARAMETER NoPush
    Commit only.

.PARAMETER DryRun
    Print every command without running it.

.PARAMETER Yes
    Skip the interactive confirmation.

.PARAMETER AllowMain
    Permit committing/pushing directly to the base branch (normally refused).

.PARAMETER NoLog
    Do not write a transcript file.

.EXAMPLE
    pwsh -File scripts/push.ps1 -Type fix -Scope ui -Description "guard empty app names" -Pr

.EXAMPLE
    pwsh -File scripts/push.ps1 -Message "chore(tooling): tweak build" -NoPush -DryRun
#>
[CmdletBinding()]
param(
    [ValidateSet('feat', 'fix', 'refactor', 'perf', 'docs', 'test', 'chore', 'build', 'ci', 'data', 'revert')]
    [string]$Type,
    [string]$Scope,
    [string]$Description,
    [string]$Message,
    [string]$Body,
    [string[]]$Files,
    [string[]]$Exclude,
    [string]$Branch,
    [string]$Base = 'main',
    [switch]$IncludeUntracked,
    [switch]$IncludeData,
    [switch]$Pr,
    [switch]$NoAutoMerge,
    [int]$WaitSeconds = 0,
    [switch]$FetchErrors,
    [switch]$Rebase,
    [switch]$Verify,
    [switch]$SkipTests,
    [switch]$NoPush,
    [switch]$DryRun,
    [switch]$Yes,
    [switch]$AllowMain,
    [switch]$NoLog
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$script:RepoRoot = $null
$script:Pat = $null
$script:OriginalGhToken = $null
$script:HadGhToken = $false
$script:LogPath = $null
$script:TranscriptStarted = $false

$DefaultExcludes = @(
    'docs', 'data/raw', 'data/output', 'data/state/current_snapshot.json',
    'scripts/temp', '.opencode', '.env', '.env.local'
)

function Write-Step {
    param([string]$Text)
    Write-Host "`n==> $Text" -ForegroundColor Cyan
}

function Write-Info {
    param([string]$Text)
    Write-Host "    $Text" -ForegroundColor DarkGray
}

function Write-Warn {
    param([string]$Text)
    Write-Host "!!  $Text" -ForegroundColor Yellow
}

function Protect-Text {
    param([string]$Text)
    if ([string]::IsNullOrEmpty($Text)) { return $Text }
    if ([string]::IsNullOrEmpty($script:Pat)) { return $Text }
    return $Text.Replace($script:Pat, '***')
}

function Invoke-Native {
    param(
        [Parameter(Mandatory)][string]$Exe,
        [string[]]$Arguments = @(),
        [switch]$AllowFail
    )

    $printed = Protect-Text ("{0} {1}" -f $Exe, ($Arguments -join ' '))
    if ($DryRun) {
        Write-Info "[dry-run] $printed"
        return [pscustomobject]@{ ExitCode = 0; Output = @() }
    }

    $raw = & $Exe @Arguments 2>&1
    $code = $LASTEXITCODE
    $lines = @($raw | ForEach-Object { Protect-Text ($_ | Out-String).TrimEnd() })
    if ($code -ne 0 -and -not $AllowFail) {
        throw "$Exe failed with exit code $code`n$($lines -join "`n")"
    }
    return [pscustomobject]@{ ExitCode = $code; Output = $lines }
}

function Get-DotEnvValue {
    param([Parameter(Mandatory)][string]$Key)
    $path = Join-Path $script:RepoRoot '.env'
    if (-not (Test-Path -LiteralPath $path)) { return $null }
    $pattern = '^\s*' + [regex]::Escape($Key) + '\s*=\s*(.*)$'
    foreach ($line in (Get-Content -LiteralPath $path)) {
        if ($line -match $pattern) {
            return $Matches[1].Trim().Trim('"').Trim("'")
        }
    }
    return $null
}

function Get-Pat {
    $candidates = @(
        $env:GITHUB_TOKEN,
        (Get-DotEnvValue -Key 'GITHUB_TOKEN'),
        $env:GH_TOKEN,
        (Get-DotEnvValue -Key 'GH_TOKEN'),
        $env:GH_PAT
    )
    foreach ($candidate in $candidates) {
        if (-not [string]::IsNullOrWhiteSpace($candidate)) {
            return $candidate.Trim()
        }
    }
    throw 'GitHub token missing: set GITHUB_TOKEN (in the environment or .env), or GH_TOKEN/GH_PAT.'
}

function Assert-Pat {
    $headers = @{
        Authorization          = "Bearer $script:Pat"
        Accept                 = 'application/vnd.github+json'
        'User-Agent'           = 'MorpheTracker-push'
        'X-GitHub-Api-Version' = '2022-11-28'
    }
    if ($DryRun) {
        Write-Info '[dry-run] GET https://api.github.com/user'
        return
    }
    try {
        $user = Invoke-RestMethod -Uri 'https://api.github.com/user' -Headers $headers -TimeoutSec 20
    }
    catch {
        throw "PAT validation failed: $(Protect-Text $_.Exception.Message)"
    }
    Write-Info "Authenticated as $($user.login)"
}

function Assert-Repo {
    $root = (& git rev-parse --show-toplevel 2>$null)
    if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($root)) {
        throw 'Not inside a git repository.'
    }
    $script:RepoRoot = (Resolve-Path -LiteralPath $root.Trim()).Path.TrimEnd('\', '/')
    Set-Location -LiteralPath $script:RepoRoot
}

function Get-CurrentBranch {
    $name = (& git rev-parse --abbrev-ref HEAD 2>$null)
    if ($LASTEXITCODE -ne 0) { throw 'Cannot determine the current branch (detached HEAD?).' }
    return $name.Trim()
}

function Build-CommitMessage {
    if (-not [string]::IsNullOrWhiteSpace($Message)) {
        $subject = ($Message -split "`n")[0].Trim()
        if ($subject -notmatch '^(feat|fix|refactor|perf|docs|test|chore|build|ci|data|revert)(\([^)]+\))?!?: \S') {
            throw "Commit subject does not follow conventional commits: '$subject'"
        }
        return $Message.Trim()
    }

    if ([string]::IsNullOrWhiteSpace($Type)) { throw '-Type is required unless -Message is supplied.' }
    if ([string]::IsNullOrWhiteSpace($Description)) { throw '-Description is required unless -Message is supplied.' }

    $subject = if ([string]::IsNullOrWhiteSpace($Scope)) {
        "$Type`: $Description"
    }
    else {
        "$Type($Scope): $Description"
    }
    if ($subject.Length -gt 72) {
        Write-Warn "Subject is $($subject.Length) characters; aim for 72 or fewer."
    }
    if ([string]::IsNullOrWhiteSpace($Body)) { return $subject }
    return "$subject`n`n$($Body.Trim())"
}

function Get-StagingExcludes {
    $excludes = @($DefaultExcludes) + @($Exclude)
    if (-not $IncludeData) { $excludes += 'data/bundles' }
    return $excludes | Where-Object { -not [string]::IsNullOrWhiteSpace($_) } | Select-Object -Unique
}

function Stage-Changes {
    $excludes = Get-StagingExcludes
    $pathspecs = @('--', '.') + ($excludes | ForEach-Object { ":(exclude)$_" })

    if ($Files) {
        $verified = @()
        foreach ($file in $Files) {
            if (-not (Test-Path -LiteralPath $file)) { throw "-Files path does not exist: $file" }
            $full = (Resolve-Path -LiteralPath $file).Path
            if (-not $full.StartsWith($script:RepoRoot, [StringComparison]::OrdinalIgnoreCase)) {
                throw "-Files path is outside the repository: $file"
            }
            $verified += $file
        }
        [void](Invoke-Native git (@('add') + $verified))
        Write-Info "Staged $($verified.Count) explicit path(s)"
        return
    }

    [void](Invoke-Native git (@('add', '-u') + $pathspecs))
    if ($IncludeUntracked) {
        [void](Invoke-Native git (@('add', '-A') + $pathspecs))
    }
    else {
        $untracked = (& git ls-files --others --exclude-standard)
        if ($untracked) {
            Write-Warn 'Untracked files were not staged (use -IncludeUntracked or -Files):'
            $untracked | Select-Object -First 10 | ForEach-Object { Write-Info "  $($_)" }
        }
    }
}

function Remove-CaseCollisions {
    $staged = @(& git diff --cached --name-only)
    if (-not $staged) { return }
    $groups = $staged | Group-Object { $_.ToLowerInvariant() } | Where-Object { $_.Count -gt 1 }
    foreach ($group in $groups) {
        Write-Warn "Case-colliding paths staged (Windows cannot represent both): $($group.Group -join ', ')"
        [void](Invoke-Native git (@('reset', '-q', '--') + $group.Group))
    }
}

function Test-StagedSecrets {
    $diff = ((& git diff --cached -U0) | Out-String)
    if ([string]::IsNullOrWhiteSpace($diff)) { return }
    $patterns = @(
        'ghp_[A-Za-z0-9]{20,}',
        'github_pat_[A-Za-z0-9_]{20,}',
        'sk-[A-Za-z0-9]{20,}',
        'AKIA[0-9A-Z]{16}',
        '-----BEGIN [A-Z ]*PRIVATE KEY-----',
        '(?i)\b(GITHUB_TOKEN|GH_PAT|GITLAB_TOKEN|TG_TOKEN|TG_CHAT)\s*=\s*\S+'
    )
    $stagedNames = @(& git diff --cached --name-only)
    foreach ($name in $stagedNames) {
        if ($name -match '(^|/)(\.env)(\.|$)' ) {
            throw "Refusing to commit environment file: $name"
        }
    }
    foreach ($pattern in $patterns) {
        $hit = [regex]::Match($diff, $pattern)
        if ($hit.Success) {
            $line = ($diff -split "`n" | Where-Object { $_ -match $pattern } | Select-Object -First 1)
            throw "Possible secret staged (pattern $pattern): $(Protect-Text $line)"
        }
    }
}

function Get-Head-Status {
    [void](Invoke-Native git @('fetch', 'origin', '--quiet'))
    $counts = (& git rev-list --left-right --count "origin/$Base...HEAD") -split '\s+'
    return [pscustomobject]@{ Behind = [int]$counts[0]; Ahead = [int]$counts[1] }
}

function Invoke-Verification {
    if ($SkipTests -and -not $Verify) {
        Write-Info 'Skipping checks (-SkipTests)'
        return
    }
    Write-Step 'Running Python tests'
    [void](Invoke-Native python @('-m', 'pytest', 'tests/', '-q'))
    if ($Verify) {
        Write-Step 'Running production build'
        [void](Invoke-Native npm @('run', 'build'))
    }
}

function New-PrBody {
    param([string]$Subject)
    if (-not [string]::IsNullOrWhiteSpace($Body)) { return $Body.Trim() }
    $commits = (& git log --oneline "origin/$Base..HEAD")
    $bullets = ($commits | ForEach-Object { "- $_" }) -join "`n"
    return "## Summary`n`n$Subject`n`n## Commits`n`n$bullets`n"
}

function Show-CiErrors {
    param([int]$PrNumber)
    Write-Step 'Collecting CI status'
    $runs = Invoke-Native gh @('run', 'list', '--branch', $Branch, '--limit', '5',
        '--json', 'databaseId,status,conclusion,workflowName,url') -AllowFail
    if ($runs.ExitCode -eq 0 -and $runs.Output) {
        try {
            $parsed = ($runs.Output -join "`n") | ConvertFrom-Json
        }
        catch { $parsed = @() }
        foreach ($run in @($parsed)) {
            $marker = if ($run.conclusion -eq 'success') { 'ok  ' } elseif ($run.status -ne 'completed') { 'run ' } else { 'FAIL' }
            Write-Info "$marker $($run.workflowName) ($($run.databaseId)) $($run.url)"
            if ($run.conclusion -notin @('success', 'skipped', 'neutral', $null)) {
                $log = Invoke-Native gh @('run', 'view', "$($run.databaseId)", '--log-failed') -AllowFail
                $tail = $log.Output | Select-Object -Last 60
                Write-Host ($tail -join "`n") -ForegroundColor DarkRed
            }
        }
    }

    if ($PrNumber -gt 0) {
        $checks = Invoke-Native gh @('pr', 'checks', "$PrNumber") -AllowFail
        if ($checks.Output) { $checks.Output | ForEach-Object { Write-Info $_ } }
        $status = Invoke-Native gh @('pr', 'view', "$PrNumber", '--json', 'state,mergeStateStatus,mergeable,url') -AllowFail
        if ($status.Output) { Write-Info ($status.Output -join ' ') }
    }
}

function Wait-ForPr {
    param([int]$PrNumber)
    if ($WaitSeconds -le 0) {
        $view = Invoke-Native gh @('pr', 'view', "$PrNumber", '--json', 'url') -AllowFail
        if ($view.Output) { Write-Info "PR: $($view.Output -join '')" }
        return $true
    }

    $deadline = (Get-Date).AddSeconds($WaitSeconds)
    while ((Get-Date) -lt $deadline) {
        $info = Invoke-Native gh @('pr', 'view', "$PrNumber", '--json', 'state,mergeStateStatus') -AllowFail
        if ($info.ExitCode -eq 0 -and $info.Output) {
            $json = ($info.Output -join "`n") | ConvertFrom-Json
            Write-Info "state=$($json.state) mergeStateStatus=$($json.mergeStateStatus)"
            if ($json.state -eq 'MERGED') { return $true }
            if ($json.state -eq 'CLOSED') { throw 'PR was closed without merging.' }
        }
        Start-Sleep -Seconds 10
    }
    throw "Timed out after ${WaitSeconds}s waiting for PR #$PrNumber."
}

function Open-Or-Get-Pr {
    param([string]$Subject)
    $existing = Invoke-Native gh @('pr', 'list', '--head', $Branch, '--state', 'open',
        '--json', 'number,url') -AllowFail
    if ($existing.ExitCode -eq 0 -and $existing.Output) {
        try { $list = ($existing.Output -join "`n") | ConvertFrom-Json } catch { $list = @() }
        $first = @($list) | Select-Object -First 1
        if ($first) {
            Write-Info "Reusing open PR #$($first.number): $($first.url)"
            return [int]$first.number
        }
    }

    $bodyFile = Join-Path ([System.IO.Path]::GetTempPath()) ("morphe-pr-{0}.md" -f ([guid]::NewGuid()))
    try {
        (New-PrBody -Subject $Subject) | Set-Content -LiteralPath $bodyFile -Encoding utf8
        $created = Invoke-Native gh @('pr', 'create', '--base', $Base, '--head', $Branch,
            '--title', $Subject, '--body-file', $bodyFile)
        $url = ($created.Output | Select-Object -Last 1)
        Write-Info "Opened $url"
        $view = Invoke-Native gh @('pr', 'view', $Branch, '--json', 'number') -AllowFail
        if ($view.ExitCode -ne 0) { throw 'PR created but its number could not be read.' }
        return [int](($view.Output -join "`n") | ConvertFrom-Json).number
    }
    finally {
        Remove-Item -LiteralPath $bodyFile -ErrorAction SilentlyContinue
    }
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
$exitCode = 0
$prNumber = 0
$subject = $null

try {
    Assert-Repo

    if (-not $NoLog) {
        $logDir = Join-Path ([System.IO.Path]::GetTempPath()) 'opencode'
        New-Item -ItemType Directory -Path $logDir -Force | Out-Null
        $script:LogPath = Join-Path $logDir ("morphe-push-{0}.log" -f (Get-Date -Format 'yyyyMMdd-HHmmss'))
        try { Start-Transcript -LiteralPath $script:LogPath -Force | Out-Null; $script:TranscriptStarted = $true }
        catch { Write-Warn "Could not start transcript: $($_.Exception.Message)" }
    }

    $script:Pat = Get-Pat
    $script:HadGhToken = Test-Path Env:GH_TOKEN
    if ($script:HadGhToken) { $script:OriginalGhToken = $env:GH_TOKEN }
    $env:GH_TOKEN = $script:Pat

    Write-Step 'Preflight'
    Assert-Pat
    $current = Get-CurrentBranch
    Write-Info "Repository: $script:RepoRoot"
    Write-Info "Branch: $current"

    if ($Branch -and $Branch -ne $current) {
        $exists = (& git rev-parse --verify --quiet "refs/heads/$Branch")
        if ($LASTEXITCODE -eq 0 -and $exists) {
            [void](Invoke-Native git @('switch', $Branch))
        }
        else {
            [void](Invoke-Native git @('switch', '-c', $Branch))
        }
        $current = $Branch
        Write-Info "Switched to $current"
    }
    $Branch = $current

    $baseBranch = $Base
    if ($Branch -eq $baseBranch -and -not $AllowMain) {
        throw "Refusing to push to the protected branch '$Branch'. Use -Branch <name> (or -AllowMain)."
    }

    $divergence = Get-Head-Status
    if ($divergence.Behind -gt 0) {
        if ($Rebase) {
            Write-Step "Rebasing onto origin/$baseBranch"
            # autostash keeps unrelated local edits (docs, data artifacts) safe
            [void](Invoke-Native git @('-c', 'rebase.autoStash=true', 'pull', '--rebase', 'origin', $baseBranch))
        }
        else {
            throw "Branch is $($divergence.Behind) commit(s) behind origin/$baseBranch. Re-run with -Rebase."
        }
    }
    Write-Info "Ahead of origin/$baseBranch by $($divergence.Ahead) commit(s)"

    Write-Step 'Staging'
    Stage-Changes
    Remove-CaseCollisions
    Test-StagedSecrets

    $staged = @(& git diff --cached --name-only)
    if ($staged.Count -eq 0) {
        Write-Warn 'Nothing staged; skipping commit.'
    }
    else {
        Write-Info "Staged $($staged.Count) file(s):"
        $staged | Select-Object -First 20 | ForEach-Object { Write-Info "  $_" }
        if ($staged.Count -gt 20) { Write-Info "  … $($staged.Count - 20) more" }
    }

    Invoke-Verification

    $commitMessage = Build-CommitMessage
    $subject = ($commitMessage -split "`n")[0]

    if ($staged.Count -gt 0) {
        if (-not $Yes -and -not $DryRun) {
            Write-Host "`nCommit as: $subject" -ForegroundColor White
            $answer = Read-Host 'Proceed? [y/N]'
            if ($answer -notmatch '^[Yy]') { throw 'Aborted by user.' }
        }
        Write-Step 'Committing'
        $msgFile = Join-Path ([System.IO.Path]::GetTempPath()) ("morphe-msg-{0}.txt" -f ([guid]::NewGuid()))
        try {
            Set-Content -LiteralPath $msgFile -Value $commitMessage -Encoding utf8
            [void](Invoke-Native git @('commit', '-F', $msgFile))
        }
        finally {
            Remove-Item -LiteralPath $msgFile -ErrorAction SilentlyContinue
        }
    }

    $ahead = (& git rev-list --count "origin/$baseBranch..HEAD")
    if ($NoPush) {
        Write-Step 'Done (-NoPush)'
        Write-Info "HEAD is $ahead commit(s) ahead of origin/$baseBranch"
    }
    elseif ([int]$ahead -eq 0) {
        Write-Warn 'No commits to push.'
    }
    else {
        Write-Step "Pushing $Branch"
        $helper = 'credential.helper=!f(){ echo username=x-access-token; echo password=$GH_TOKEN; }; f'
        [void](Invoke-Native git @(
                '-c', 'credential.helper=',
                '-c', $helper,
                'push', '-u', 'origin', $Branch
            ))
        Write-Info 'Pushed without persisting the token in git config.'

        if ($Pr) {
            Write-Step 'Pull request'
            $prNumber = Open-Or-Get-Pr -Subject $subject
            if (-not $NoAutoMerge) {
                $merge = Invoke-Native gh @('pr', 'merge', "$prNumber", '--auto', '--merge') -AllowFail
                if ($merge.ExitCode -ne 0) {
                    Write-Warn 'Auto-merge could not be armed (already queued or checks pending).'
                    $merge.Output | ForEach-Object { Write-Info $_ }
                }
            }
            else {
                Write-Info 'Auto-merge disabled (-NoAutoMerge)'
            }
            if ($FetchErrors -or $WaitSeconds -gt 0) { Show-CiErrors -PrNumber $prNumber }
            [void](Wait-ForPr -PrNumber $prNumber)
        }
        elseif ($FetchErrors) {
            Show-CiErrors -PrNumber 0
        }
    }
}
catch {
    $exitCode = 1
    Write-Host "`nFAILED: $(Protect-Text $_.Exception.Message)" -ForegroundColor Red
    try {
        $status = & git status --short 2>$null
        if ($status) { Write-Host ($status -join "`n") -ForegroundColor DarkYellow }
    }
    catch { }
}
finally {
    if ($script:HadGhToken) { $env:GH_TOKEN = $script:OriginalGhToken }
    else { Remove-Item Env:GH_TOKEN -ErrorAction SilentlyContinue }

    if ($script:TranscriptStarted) {
        try { Stop-Transcript | Out-Null } catch { }
        if ($script:LogPath -and (Test-Path -LiteralPath $script:LogPath)) {
            Write-Host "`nTranscript: $($script:LogPath)" -ForegroundColor DarkGray
        }
    }
    if ($prNumber -gt 0) { Write-Host "PR: #$prNumber" -ForegroundColor DarkGray }
    exit $exitCode
}
