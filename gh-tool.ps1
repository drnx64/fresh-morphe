<#
.SYNOPSIS
    End-to-end Git and GitHub workflow automation: stage, commit, branch, push, tag, pull request, issue, release, status.

.DESCRIPTION
    gh-tool.ps1 drives native git commands and the GitHub REST API (api.github.com) from one file.

    Execution order (each step runs only when its flags are present):
        1. Validate environment (git, repository, GITHUB_TOKEN)
        2. Switch or create branch   (-Branch)
        3. Stage                     (-All | -Path)
        4. Compose commit subject    (-Type / -Scope, shortcuts expanded)
        5. Commit                    (-Message)
        6. Push branch               (-Force uses --force-with-lease)
        7. Create and push tag       (-Tag)
        8. Pull request / issue / release via GitHub API
        9. Enable auto-merge         (-AutoMerge, needs -Pr and the gh CLI)

    The branch switch runs BEFORE staging and committing so the commit lands on the target branch.
    Running it after the commit would leave the same commit on both the old and the new branch.

    Token lookup order: GITHUB_TOKEN in .env (current directory, then repository root),
    then the GITHUB_TOKEN environment variable. Only GITHUB_TOKEN is read from .env.
    The token is never printed, never placed in a URL, and never passed to git.
    Files named .env are always excluded from -All staging.

    Commit subjects can be composed from shortcuts (-Type / -Scope) or written directly:
        .\gh-tool.ps1 -m "add chips" -t f -s u    ->  feat(ui): add chips
        .\gh-tool.ps1 -m "fx(ui): drop legacy"    ->  fix(ui): drop legacy
    Type shortcuts: f feat, fx fix, ch chore, d docs, t test, rf refactor, pf perf,
    s style, b build, ci ci, rl revert. Scope shortcuts: u ui, d data, p pipeline,
    lg logic, s server, b build, dp deps, tl tooling, ic icons, ci ci.
    A unique prefix also resolves (ch -> chore, ref -> refactor); an ambiguous prefix
    such as "re" (refactor or revert) is rejected. Unknown lowercase scopes are kept
    as literal scopes, so -Scope api works. Only the subject line is rewritten and the
    rest of the message is preserved. A trailing '!' keeps the breaking-change marker.
    A message that already starts with a conventional prefix is never prefixed twice,
    and -Type / -Scope that disagree with that prefix are reported as conflicts.

    Long GNU-style flags are accepted and translated to PowerShell parameters:
        --message --type --scope --all --path --paths --force --branch --pr --pr-title
        --pr-body --draft --auto-merge --issue --issue-title --issue-body --tag --release
        --status --dry-run --verbose-log
    Both "--flag value" and "--flag=value" forms work. Use "--paths a b c" for several
    paths: PowerShell binds "--path" to -Path directly and then only takes one value.
    For the same reason prefer "--paths a b" over "-Path a,b" when the call comes from
    a native command line that does not parse commas.

    Bootstrap behavior at the top of the file:
        - Under an AllSigned or Restricted execution policy the script relaunches itself in a new
          process with a process-scoped Bypass policy. Machine and user policy are not changed.
        - A downloaded script carrying the internet zone mark is unblocked.
        - On Windows, when the current user cannot write to the .git directory and the session is
          not elevated, the script relaunches itself through a UAC prompt in a new window.
    A policy that blocks the file from loading at all cannot be bypassed from inside the file.
    In that case start it once with: powershell -ExecutionPolicy Bypass -File .\gh-tool.ps1 ...

.PARAMETER Message
    Commit message. Alias: -m. Commits what is staged. Combine with -All or -Path to stage first.

.PARAMETER Type
    Conventional commit type, added to the subject as "<type>: <message>". Alias: -t.
    Shortcuts: f feat, fx fix, ch chore, d docs, t test, rf refactor, pf perf, s style,
    b build, ci ci, rl revert. A unique prefix also works. Requires -Message.

.PARAMETER Scope
    Conventional commit scope, rendered as "<type>(<scope>): <message>". Alias: -s.
    Shortcuts: u ui, d data, p pipeline, lg logic, s server, b build, dp deps,
    tl tooling, ic icons, ci ci. Other lowercase values are kept as literal scopes.
    Requires a type: either -Type or a conventional prefix already in -Message.

.PARAMETER All
    Stage all untracked and modified files (git add -A) before committing. Alias: -a.

.PARAMETER Path
    Files or directories to stage before committing. Alias: -p. Requires -Message.

.PARAMETER Force
    Force push using --force-with-lease. Alias: -f.

.PARAMETER Branch
    Branch to switch to, or create from the current HEAD, before committing and pushing. Alias: -b.

.PARAMETER Pr
    Create a pull request from the target branch into the repository default branch after pushing.

.PARAMETER PrTitle
    Pull request title. Defaults to the subject of the last commit message.

.PARAMETER PrBody
    Pull request body. Defaults to the full last commit message.

.PARAMETER Draft
    Create the pull request as a draft. Requires -Pr.

.PARAMETER AutoMerge
    After -Pr, enable auto-merge (squash-free merge commit) so the pull request merges
    once required checks pass. Uses the gh CLI, authenticating with the same token.
    Requires -Pr and cannot be combined with -Draft.

.PARAMETER Issue
    Create a GitHub issue. Requires -IssueTitle.

.PARAMETER IssueTitle
    Issue title.

.PARAMETER IssueBody
    Issue body.

.PARAMETER Tag
    Create an annotated tag at HEAD and push it. An existing tag is reused when -Release is present.

.PARAMETER Release
    Create a GitHub Release from the tag with generated release notes. Requires -Tag.

.PARAMETER Status
    Print a repository summary: branch, working tree, ahead/behind counts, open pull requests.
    Cannot be combined with workflow parameters.

.PARAMETER DryRun
    Print every action without changing the repository or calling a GitHub API endpoint.
    Read-only git commands still run. API requests and local writes are skipped.

.PARAMETER VerboseLog
    Print debug lines, including every git command and API endpoint. Alias: -v.

.EXAMPLE
    .\gh-tool.ps1 -m "Fix login redirect" -a
    Stage everything, commit, and push the current branch.

.EXAMPLE
    .\gh-tool.ps1 --message "Add parser" --path src\parser.ps1 tests --branch feature/parser --pr
    Create feature/parser, stage two paths, commit, push, and open a pull request.

.EXAMPLE
    .\gh-tool.ps1 -m "Rework API client" -a --pr --draft --pr-title "Rework API client" --pr-body "Part 1 of 3"
    Commit everything and open a draft pull request with a custom title and body.

.EXAMPLE
    .\gh-tool.ps1 -m "Rebase cleanup" -a -f
    Commit and force push with --force-with-lease.

.EXAMPLE
    .\gh-tool.ps1 --issue --issue-title "Crash on empty input" --issue-body "Steps: run with no arguments."
    Create an issue without touching the repository.

.EXAMPLE
    .\gh-tool.ps1 --tag v1.4.0 --release
    Create and push tag v1.4.0, then publish a GitHub Release with generated notes.

.EXAMPLE
    .\gh-tool.ps1 --status
    Show branch, working tree changes, ahead/behind counts, and open pull requests.

.EXAMPLE
    .\gh-tool.ps1 -m "add chips" -a -t f -s u
    Shortcuts expand to the conventional subject "feat(ui): add chips" before committing.

.EXAMPLE
    .\gh-tool.ps1 -m "fx(ui): stop double render" -p scripts\ui.js --branch fix/ui-render --pr --auto-merge
    Expand the shortcut inside the message, then open the pull request and enable auto-merge.

.EXAMPLE
    .\gh-tool.ps1 -b chore/cleanup -p scripts --message "ch(tooling): drop dead helpers" -t ch -s tooling --pr --auto-merge
    Write the prefix by hand or pass -Type/-Scope; both compose the same subject.

.EXAMPLE
    .\gh-tool.ps1 -m "Preview only" -a --pr --dry-run -v
    Show every command and request that would run, without changing anything.

.NOTES
    Format of the .env file (one line is enough):
        GITHUB_TOKEN=ghp_yourTokenHere
    Required token permissions:
        Classic token: repo scope.
        Fine-grained token: Contents, Pull requests, and Issues read/write on the repository.
    Exit codes: 0 success (warnings included), 1 failure.
    Version: 1.1.0
#>
#Requires -Version 5.1
[CmdletBinding(PositionalBinding = $false, DefaultParameterSetName = 'Workflow')]
param(
    [Parameter(ParameterSetName = 'Workflow')]
    [Alias('m')]
    [ValidateNotNullOrEmpty()]
    [string]$Message,

    [Parameter(ParameterSetName = 'Workflow')]
    [Alias('t')]
    [string]$Type,

    [Parameter(ParameterSetName = 'Workflow')]
    [Alias('s')]
    [string]$Scope,

    [Parameter(ParameterSetName = 'Workflow')]
    [Alias('a')]
    [switch]$All,

    [Parameter(ParameterSetName = 'Workflow')]
    [Alias('p')]
    [string[]]$Path,

    [Parameter(ParameterSetName = 'Workflow')]
    [Alias('f')]
    [switch]$Force,

    [Parameter(ParameterSetName = 'Workflow')]
    [Alias('b')]
    [ValidateNotNullOrEmpty()]
    [string]$Branch,

    [Parameter(ParameterSetName = 'Workflow')]
    [switch]$Pr,

    [Parameter(ParameterSetName = 'Workflow')]
    [string]$PrTitle,

    [Parameter(ParameterSetName = 'Workflow')]
    [string]$PrBody,

    [Parameter(ParameterSetName = 'Workflow')]
    [switch]$Draft,

    [Parameter(ParameterSetName = 'Workflow')]
    [switch]$AutoMerge,

    [Parameter(ParameterSetName = 'Workflow')]
    [switch]$Issue,

    [Parameter(ParameterSetName = 'Workflow')]
    [string]$IssueTitle,

    [Parameter(ParameterSetName = 'Workflow')]
    [string]$IssueBody,

    [Parameter(ParameterSetName = 'Workflow')]
    [string]$Tag,

    [Parameter(ParameterSetName = 'Workflow')]
    [switch]$Release,

    [Parameter(ParameterSetName = 'Status', Mandatory)]
    [switch]$Status,

    [switch]$DryRun,

    [Alias('v')]
    [switch]$VerboseLog,

    [Parameter(ValueFromRemainingArguments)]
    [string[]]$RemainingArguments = @(),

    [Parameter(DontShow)]
    [switch]$BootstrapCompleted
)

Set-StrictMode -Version 3.0
$ErrorActionPreference = 'Stop'

# ----------------------------------------------------------------------------
# Constants
# ----------------------------------------------------------------------------
$TOOL_NAME = 'gh-tool'
$TOOL_VERSION = '1.1.0'
$GITHUB_API_BASE_URL = 'https://api.github.com'
$GITHUB_WEB_BASE_URL = 'https://github.com'
$GITHUB_API_VERSION = '2022-11-28'
$GITHUB_API_TIMEOUT_SECONDS = 30
$USER_AGENT = "$TOOL_NAME.ps1/$TOOL_VERSION"
$TOKEN_VARIABLE_NAME = 'GITHUB_TOKEN'
$ENV_FILE_NAME = '.env'
$DEFAULT_REMOTE_NAME = 'origin'
$FALLBACK_DEFAULT_BRANCH = 'main'
$MAX_LISTED_PULL_REQUESTS = 100
$MAX_ERROR_DETAIL_LINES = 6
$LOG_TAG_WIDTH = 11
$TREE_INDENT = '           '
$GIT_NETWORK_OPTIONS = @('-c', 'http.lowSpeedLimit=1000', '-c', 'http.lowSpeedTime=60')
$GIT_REMOTE_PROBE_OPTIONS = @('-c', 'http.lowSpeedLimit=1000', '-c', 'http.lowSpeedTime=15')

$GLYPH_BRANCH = [string][char]0x2523
$GLYPH_LAST = [string][char]0x2517
$GLYPH_SUCCESS = [string][char]0x2714
$GLYPH_FAILURE = [string][char]0x2716
$GLYPH_WARNING = [string][char]0x26A0
$GLYPH_PLANNED = [string][char]0x25CB
$GLYPH_RULE = [string][char]0x2500

$LOG_LEVEL_COLORS = @{
    INFO    = 'Cyan'
    SUCCESS = 'Green'
    WARN    = 'Yellow'
    ERROR   = 'Red'
    DEBUG   = 'DarkGray'
    DRYRUN  = 'Magenta'
}

$GNU_OPTION_TABLE = @{
    '--message'     = @{ Parameter = 'Message';    ValueKind = 'Single' }
    '--type'        = @{ Parameter = 'Type';       ValueKind = 'Single' }
    '--scope'       = @{ Parameter = 'Scope';      ValueKind = 'Single' }
    '--all'         = @{ Parameter = 'All';        ValueKind = 'None' }
    '--path'        = @{ Parameter = 'Path';       ValueKind = 'List' }
    '--paths'       = @{ Parameter = 'Path';       ValueKind = 'List' }
    '--force'       = @{ Parameter = 'Force';      ValueKind = 'None' }
    '--branch'      = @{ Parameter = 'Branch';     ValueKind = 'Single' }
    '--pr'          = @{ Parameter = 'Pr';         ValueKind = 'None' }
    '--pr-title'    = @{ Parameter = 'PrTitle';    ValueKind = 'Single' }
    '--pr-body'     = @{ Parameter = 'PrBody';     ValueKind = 'Single' }
    '--draft'       = @{ Parameter = 'Draft';      ValueKind = 'None' }
    '--auto-merge'  = @{ Parameter = 'AutoMerge';  ValueKind = 'None' }
    '--issue'       = @{ Parameter = 'Issue';      ValueKind = 'None' }
    '--issue-title' = @{ Parameter = 'IssueTitle'; ValueKind = 'Single' }
    '--issue-body'  = @{ Parameter = 'IssueBody';  ValueKind = 'Single' }
    '--tag'         = @{ Parameter = 'Tag';        ValueKind = 'Single' }
    '--release'     = @{ Parameter = 'Release';    ValueKind = 'None' }
    '--status'      = @{ Parameter = 'Status';     ValueKind = 'None' }
    '--dry-run'     = @{ Parameter = 'DryRun';     ValueKind = 'None' }
    '--verbose-log' = @{ Parameter = 'VerboseLog'; ValueKind = 'None' }
}

$COMMIT_TYPE_SHORTCUTS = [ordered]@{
    'f'  = 'feat'
    'fx' = 'fix'
    'ch' = 'chore'
    'd'  = 'docs'
    't'  = 'test'
    'rf' = 'refactor'
    'pf' = 'perf'
    's'  = 'style'
    'b'  = 'build'
    'ci' = 'ci'
    'rl' = 'revert'
}

$COMMIT_SCOPE_SHORTCUTS = [ordered]@{
    'u'  = 'ui'
    'd'  = 'data'
    'p'  = 'pipeline'
    'lg' = 'logic'
    's'  = 'server'
    'b'  = 'build'
    'dp' = 'deps'
    'tl' = 'tooling'
    'ic' = 'icons'
    'ci' = 'ci'
}

$CONVENTIONAL_COMMIT_TYPES = @('build', 'chore', 'ci', 'docs', 'feat', 'fix', 'perf', 'refactor', 'revert', 'style', 'test')
$CONVENTIONAL_SUBJECT_PATTERN = '^(?<type>[A-Za-z][A-Za-z0-9]*)(?<scope>\([^)]*\))?(?<breaking>!)?\s*:\s+(?<subject>\S.*)$'
$COMMIT_SCOPE_PATTERN = '^[a-z0-9][a-z0-9._/-]*$'

$GIT_FAILURE_PATTERNS = @(
    @{
        Pattern = 'CONFLICT|Automatic merge failed|unmerged|would be overwritten by (checkout|merge)|needs merge'
        Summary = 'Merge conflict or conflicting local changes'
        Hint    = 'Resolve, commit, or stash the conflicting files (git status), then run the tool again.'
    }
    @{
        Pattern = 'Authentication failed|could not read Username|terminal prompts disabled|Permission denied \(publickey\)|invalid credentials|\b403\b'
        Summary = 'Git authentication failed'
        Hint    = 'Sign in with Git Credential Manager or an SSH key that has write access to this repository.'
    }
    @{
        Pattern = 'stale info'
        Summary = 'The remote branch changed since your last fetch, so --force-with-lease refused to overwrite it'
        Hint    = 'Run "git fetch", review the new remote commits, then retry.'
    }
    @{
        Pattern = 'non-fast-forward|fetch first|\[rejected\]|Updates were rejected'
        Summary = 'The remote branch contains commits that are not in your local branch'
        Hint    = 'Run "git pull --rebase", then retry. Use -Force only when overwriting the remote branch is intended.'
    }
    @{
        Pattern = 'protected branch|GH006|GH013'
        Summary = 'The remote branch is protected'
        Hint    = 'Push to a different branch with -Branch and open a pull request with -Pr.'
    }
    @{
        Pattern = 'Could not resolve host|Failed to connect|timed out|Connection (refused|reset)|unable to access|Operation too slow|RPC failed|Network is unreachable'
        Summary = 'Network failure or timeout while contacting the remote'
        Hint    = 'Check the network connection, proxy, and VPN, then retry.'
    }
    @{
        Pattern = 'does not appear to be a git repository|No such remote|Could not read from remote repository|repository not found'
        Summary = 'The remote is missing or unreachable'
        Hint    = 'Check the remote URL with "git remote -v" and confirm the repository exists and you have access.'
    }
    @{
        Pattern = 'Author identity unknown|unable to auto-detect email|empty ident name'
        Summary = 'Git user identity is not configured'
        Hint    = 'Run: git config --global user.name "Your Name"; git config --global user.email "you@example.com"'
    }
    @{
        Pattern = 'hook (declined|failed)|pre-commit|commit-msg'
        Summary = 'A Git hook rejected the operation'
        Hint    = 'Fix the problem reported by the hook output above, then retry.'
    }
    @{
        Pattern = "pathspec '.*' did not match|did not match any files"
        Summary = 'A path or branch name did not match anything'
        Hint    = 'Check the spelling and that the path exists relative to the current directory.'
    }
)

# ----------------------------------------------------------------------------
# Logging
# ----------------------------------------------------------------------------
function Write-LogMessage {
    param(
        [Parameter(Mandatory)][ValidateSet('INFO', 'SUCCESS', 'WARN', 'ERROR', 'DEBUG', 'DRYRUN')][string]$Level,
        [Parameter(Mandatory)][AllowEmptyString()][string]$Text
    )
    if ($Level -eq 'DEBUG' -and -not $script:IsVerboseLoggingEnabled) { return }

    $tag = ('[{0}]' -f $Level).PadRight($LOG_TAG_WIDTH)
    Write-Host $tag -ForegroundColor $LOG_LEVEL_COLORS[$Level] -NoNewline
    Write-Host $Text
}

function Write-LogInfo    { param([string]$Text) Write-LogMessage -Level 'INFO' -Text $Text }
function Write-LogSuccess { param([string]$Text) Write-LogMessage -Level 'SUCCESS' -Text $Text }
function Write-LogWarning { param([string]$Text) Write-LogMessage -Level 'WARN' -Text $Text }
function Write-LogError   { param([string]$Text) Write-LogMessage -Level 'ERROR' -Text $Text }
function Write-LogDebug   { param([string]$Text) Write-LogMessage -Level 'DEBUG' -Text $Text }
function Write-LogDryRun  { param([string]$Text) Write-LogMessage -Level 'DRYRUN' -Text $Text }

function Write-TreeLine {
    param(
        [Parameter(Mandatory)][string]$Text,
        [Parameter(Mandatory)][bool]$IsLast,
        [string]$Color = 'Gray'
    )
    $connector = if ($IsLast) { $GLYPH_LAST } else { $GLYPH_BRANCH }
    Write-Host ($TREE_INDENT + $connector + ' ') -ForegroundColor DarkGray -NoNewline
    Write-Host $Text -ForegroundColor $Color
}

function Write-SectionTitle {
    param([Parameter(Mandatory)][string]$Title)
    $rule = $GLYPH_RULE * 3
    Write-Host "$rule $Title $rule" -ForegroundColor Cyan
}

function New-ToolException {
    param(
        [Parameter(Mandatory)][string]$Message,
        [string]$Hint = '',
        [string[]]$Details = @()
    )
    $exception = [System.InvalidOperationException]::new($Message)
    $exception.Data['Hint'] = $Hint
    $exception.Data['Details'] = [string[]]$Details
    return $exception
}

function Write-ToolFailure {
    param([Parameter(Mandatory)][System.Management.Automation.ErrorRecord]$ErrorRecord)

    $exception = $ErrorRecord.Exception
    Write-LogError $exception.Message

    $rows = [System.Collections.Generic.List[string]]::new()
    $hint = $exception.Data['Hint']
    if ($hint) { $rows.Add("Hint: $hint") }
    foreach ($detail in @($exception.Data['Details'])) {
        if ($detail) { $rows.Add([string]$detail) }
    }
    for ($index = 0; $index -lt $rows.Count; $index++) {
        Write-TreeLine -Text $rows[$index] -IsLast ($index -eq $rows.Count - 1) -Color Yellow
    }
    Write-LogDebug $ErrorRecord.ScriptStackTrace
}

# ----------------------------------------------------------------------------
# Bootstrap: argument translation, execution policy, elevation
# ----------------------------------------------------------------------------
function Test-IsWindowsPlatform {
    return [System.Environment]::OSVersion.Platform -eq [System.PlatformID]::Win32NT
}

function Test-IsElevatedSession {
    if (-not (Test-IsWindowsPlatform)) { return $true }
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = [Security.Principal.WindowsPrincipal]::new($identity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Get-CurrentShellPath {
    return (Get-Process -Id $PID).Path
}

function ConvertTo-SingleQuotedLiteral {
    param([Parameter(Mandatory)][AllowEmptyString()][string]$Text)
    $quoteCharacters = "(['\u2018\u2019\u201A\u201B])"
    $escaped = [regex]::Replace($Text, $quoteCharacters, '$1$1')
    return "'" + $escaped + "'"
}

function ConvertTo-RelaunchCommand {
    param(
        [Parameter(Mandatory)][string]$ScriptPath,
        [Parameter(Mandatory)][System.Collections.IDictionary]$Parameters
    )
    $segments = [System.Collections.Generic.List[string]]::new()
    $segments.Add('& ' + (ConvertTo-SingleQuotedLiteral $ScriptPath))

    foreach ($name in $Parameters.Keys) {
        $value = $Parameters[$name]
        if ($value -is [System.Management.Automation.SwitchParameter]) {
            if ($value.IsPresent) { $segments.Add("-$name") }
            continue
        }
        $quotedValues = @($value) | ForEach-Object { ConvertTo-SingleQuotedLiteral ([string]$_) }
        $segments.Add("-$name")
        $segments.Add($quotedValues -join ',')
    }
    $segments.Add('-BootstrapCompleted')
    $segments.Add('; exit $LASTEXITCODE')
    return $segments -join ' '
}

function ConvertTo-EncodedCommand {
    param([Parameter(Mandatory)][string]$CommandText)
    return [Convert]::ToBase64String([System.Text.Encoding]::Unicode.GetBytes($CommandText))
}

function Select-ForwardableParameters {
    param([Parameter(Mandatory)][System.Collections.IDictionary]$BoundParameters)
    $forwardable = @{}
    foreach ($name in $BoundParameters.Keys) {
        if ($name -in @('RemainingArguments', 'BootstrapCompleted')) { continue }
        $forwardable[$name] = $BoundParameters[$name]
    }
    return $forwardable
}

function Split-GnuOptionToken {
    param([Parameter(Mandatory)][string]$Token)
    $separatorIndex = $Token.IndexOf('=')
    if ($Token.StartsWith('--') -and $separatorIndex -gt 2) {
        return @{ Name = $Token.Substring(0, $separatorIndex); InlineValue = $Token.Substring($separatorIndex + 1) }
    }
    return @{ Name = $Token; InlineValue = $null }
}

function Convert-GnuArgumentsToParameters {
    param([Parameter(Mandatory)][string[]]$Tokens)

    $parameters = @{}
    $index = 0
    while ($index -lt $Tokens.Count) {
        $split = Split-GnuOptionToken -Token $Tokens[$index]
        $option = $GNU_OPTION_TABLE[$split.Name]
        if ($null -eq $option) {
            $validOptions = ($GNU_OPTION_TABLE.Keys | Sort-Object) -join ' '
            throw (New-ToolException -Message "Unrecognized argument '$($Tokens[$index])'." -Hint "Valid long options: $validOptions. A bare value here means the previous flag only took one value; use --paths for several paths.")
        }
        $index++

        if ($option.ValueKind -eq 'None') {
            $parameters[$option.Parameter] = $true
            continue
        }

        $values = [System.Collections.Generic.List[string]]::new()
        if ($null -ne $split.InlineValue) { $values.Add($split.InlineValue) }
        $canTakeMore = $option.ValueKind -eq 'List' -or $values.Count -eq 0
        while ($canTakeMore -and $index -lt $Tokens.Count -and -not $Tokens[$index].StartsWith('--')) {
            $values.Add($Tokens[$index])
            $index++
            $canTakeMore = $option.ValueKind -eq 'List'
        }
        if ($values.Count -eq 0) {
            throw (New-ToolException -Message "Option '$($split.Name)' requires a value.")
        }
        $parameters[$option.Parameter] = if ($option.ValueKind -eq 'List') { [string[]]$values } else { $values[0] }
    }
    return $parameters
}

function Invoke-GnuArgumentTranslation {
    param(
        [Parameter(Mandatory)][System.Collections.IDictionary]$BoundParameters,
        [Parameter(Mandatory)][string[]]$Tokens
    )
    if ([string]::IsNullOrEmpty($PSCommandPath)) {
        throw (New-ToolException -Message 'GNU-style options need the script to run from a file.' -Hint 'Use PowerShell-style parameters such as -Message and -Pr.')
    }
    $forwarded = Select-ForwardableParameters -BoundParameters $BoundParameters
    $translated = Convert-GnuArgumentsToParameters -Tokens $Tokens
    foreach ($name in $translated.Keys) { $forwarded[$name] = $translated[$name] }

    & $PSCommandPath @forwarded
    exit $LASTEXITCODE
}

function Test-ExecutionPolicyBlocksScripts {
    return (Get-ExecutionPolicy) -in @('Restricted', 'AllSigned')
}

function Unblock-ScriptFile {
    if (-not (Test-IsWindowsPlatform)) { return }
    $zoneStream = Get-Item -LiteralPath $PSCommandPath -Stream 'Zone.Identifier' -ErrorAction SilentlyContinue
    if ($zoneStream) { Unblock-File -LiteralPath $PSCommandPath }
}

function Restart-SelfWithBypassPolicy {
    param([Parameter(Mandatory)][System.Collections.IDictionary]$BoundParameters)
    $parameters = Select-ForwardableParameters -BoundParameters $BoundParameters
    $commandText = ConvertTo-RelaunchCommand -ScriptPath $PSCommandPath -Parameters $parameters

    Write-LogWarning 'Execution policy blocks scripts. Relaunching with a process-scoped Bypass policy.'
    & (Get-CurrentShellPath) -NoProfile -ExecutionPolicy Bypass -OutputFormat Text -EncodedCommand (ConvertTo-EncodedCommand $commandText)
    exit $LASTEXITCODE
}

function Test-GitDirectoryIsReadOnlyForCurrentUser {
    $gitDirectory = & git rev-parse --absolute-git-dir 2>$null
    if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($gitDirectory)) { return $false }

    $probePath = Join-Path $gitDirectory ".$TOOL_NAME-write-probe-$PID"
    try {
        [System.IO.File]::WriteAllText($probePath, '')
        Remove-Item -LiteralPath $probePath -Force
        return $false
    }
    catch [System.UnauthorizedAccessException] {
        return $true
    }
    catch {
        Write-LogDebug "Write probe inconclusive: $($_.Exception.Message)"
        return $false
    }
}

function Test-ElevationRequired {
    if (-not (Test-IsWindowsPlatform)) { return $false }
    if (Test-IsElevatedSession) { return $false }
    if (-not (Get-Command git -ErrorAction SilentlyContinue)) { return $false }
    return Test-GitDirectoryIsReadOnlyForCurrentUser
}

function Restart-SelfElevated {
    param([Parameter(Mandatory)][System.Collections.IDictionary]$BoundParameters)
    $parameters = Select-ForwardableParameters -BoundParameters $BoundParameters
    $commandText = ConvertTo-RelaunchCommand -ScriptPath $PSCommandPath -Parameters $parameters
    $shellArguments = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-NoExit', '-OutputFormat', 'Text', '-EncodedCommand', (ConvertTo-EncodedCommand $commandText))

    Write-LogWarning 'The .git directory is not writable for this user. Relaunching elevated in a new window.'
    try {
        Start-Process -FilePath (Get-CurrentShellPath) -ArgumentList $shellArguments -Verb RunAs -WorkingDirectory (Get-Location).ProviderPath | Out-Null
    }
    catch {
        throw (New-ToolException -Message 'Elevation was cancelled or denied.' -Hint 'Fix the permissions on the .git directory or start the terminal as Administrator.' -Details @($_.Exception.Message))
    }
    exit 0
}

function Invoke-Bootstrap {
    param(
        [Parameter(Mandatory)][System.Collections.IDictionary]$BoundParameters,
        [Parameter(Mandatory)][AllowEmptyCollection()][string[]]$UnrecognizedTokens,
        [Parameter(Mandatory)][bool]$IsBootstrapCompleted
    )
    if ($UnrecognizedTokens.Count -gt 0) {
        Invoke-GnuArgumentTranslation -BoundParameters $BoundParameters -Tokens $UnrecognizedTokens
    }
    if ($IsBootstrapCompleted -or [string]::IsNullOrEmpty($PSCommandPath)) { return }

    if (Test-ExecutionPolicyBlocksScripts) { Restart-SelfWithBypassPolicy -BoundParameters $BoundParameters }
    Unblock-ScriptFile
    if (Test-ElevationRequired) { Restart-SelfElevated -BoundParameters $BoundParameters }
}

$script:IsVerboseLoggingEnabled = $VerboseLog.IsPresent -or ($PSBoundParameters.ContainsKey('Verbose') -and $PSBoundParameters['Verbose'])

try {
    Invoke-Bootstrap -BoundParameters $PSBoundParameters -UnrecognizedTokens $RemainingArguments -IsBootstrapCompleted $BootstrapCompleted.IsPresent
}
catch {
    Write-ToolFailure -ErrorRecord $_
    exit 1
}

# ----------------------------------------------------------------------------
# Headline: the whole run in five lines
# ----------------------------------------------------------------------------
function Invoke-GitHubTool {
    param(
        [Parameter(Mandatory)]$Options,
        [Parameter(Mandatory)]$Report
    )
    $stopwatch = [System.Diagnostics.Stopwatch]::StartNew()
    try {
        $Report.CurrentStep = 'Validate options'
        Confirm-OptionsAreConsistent -Options $Options
        $Report.CurrentStep = 'Compose commit subject'
        Set-ConventionalCommitMessage -Options $Options
        $context = New-ToolContext -Options $Options -Report $Report
        if ($Options.Status) { Show-RepositoryStatus -Context $context }
        else { Invoke-ChangeWorkflow -Context $context }
    }
    catch {
        $Report.ExitCode = 1
        Write-ToolFailure -ErrorRecord $_
        Add-ReportStep -Report $Report -Name $Report.CurrentStep -Outcome 'Failed' -Detail $_.Exception.Message
    }
    finally {
        $stopwatch.Stop()
    }
    Write-ExecutionSummary -Report $Report -Options $Options -Elapsed $stopwatch.Elapsed
}

function Invoke-ChangeWorkflow {
    param([Parameter(Mandatory)]$Context)
    $options = $Context.Options

    if ($options.Branch) { Invoke-BranchSwitch -Context $Context }
    if ($options.All -or $options.Path.Count -gt 0) { Invoke-Staging -Context $Context }
    if ($options.Message) { Invoke-Commit -Context $Context }
    if (Test-ShouldPushBranch -Options $options) { Invoke-BranchPush -Context $Context }
    if ($options.Tag) {
        Invoke-TagCreation -Context $Context
        Invoke-TagPush -Context $Context
    }
    if ($options.Pr) { Invoke-PullRequestCreation -Context $Context }
    if ($options.Issue) { Invoke-IssueCreation -Context $Context }
    if ($options.Release) { Invoke-ReleaseCreation -Context $Context }
}

# ----------------------------------------------------------------------------
# Option validation
# ----------------------------------------------------------------------------
function Test-AnyWorkflowActionRequested {
    param([Parameter(Mandatory)]$Options)
    return [bool]($Options.Message -or $Options.All -or $Options.Path.Count -gt 0 -or $Options.Branch -or
        $Options.Force -or $Options.Pr -or $Options.Issue -or $Options.Tag -or $Options.Release)
}

function Test-ShouldPushBranch {
    param([Parameter(Mandatory)]$Options)
    return [bool]($Options.Message -or $Options.All -or $Options.Path.Count -gt 0 -or $Options.Branch -or $Options.Force -or $Options.Pr)
}

function Test-GitHubApiRequired {
    param([Parameter(Mandatory)]$Options)
    return [bool]($Options.Status -or $Options.Pr -or $Options.Issue -or $Options.Release)
}

function Get-OptionViolations {
    param([Parameter(Mandatory)]$Options)
    $violations = [System.Collections.Generic.List[string]]::new()
    $stagesFiles = $Options.All -or $Options.Path.Count -gt 0

    if (-not $Options.Status -and -not (Test-AnyWorkflowActionRequested -Options $Options)) {
        $violations.Add('No action requested. Pass at least one of -Message, -Branch, -Pr, -Issue, -Tag, -Release, -Force, -Status.')
    }
    if ($Options.All -and $Options.Path.Count -gt 0) { $violations.Add('-All and -Path cannot be combined. Choose one.') }
    if ($stagesFiles -and -not $Options.Message) { $violations.Add('-All and -Path stage files, which requires -Message.') }
    if ($Options.Message -and [string]::IsNullOrWhiteSpace($Options.Message)) {
        $violations.Add('-Message cannot be blank.')
    }
    if (($Options.PrTitle -or $Options.PrBody -or $Options.Draft) -and -not $Options.Pr) { $violations.Add('-PrTitle, -PrBody, and -Draft require -Pr.') }
    if (($Options.IssueTitle -or $Options.IssueBody) -and -not $Options.Issue) { $violations.Add('-IssueTitle and -IssueBody require -Issue.') }
    if ($Options.Issue -and [string]::IsNullOrWhiteSpace($Options.IssueTitle)) { $violations.Add('-Issue requires -IssueTitle.') }
    if ($Options.Release -and -not $Options.Tag) { $violations.Add('-Release requires -Tag.') }
    if (($Options.Type -or $Options.Scope) -and -not $Options.Message) { $violations.Add('-Type and -Scope compose the commit subject, which requires -Message.') }
    if ($Options.AutoMerge -and -not $Options.Pr) { $violations.Add('-AutoMerge requires -Pr.') }
    if ($Options.AutoMerge -and $Options.Draft) { $violations.Add('-AutoMerge cannot be combined with -Draft.') }
    foreach ($check in @(
        @{ Value = $Options.Type; Resolve = 'Resolve-CommitType'; Label = 'type' }
        @{ Value = $Options.Scope; Resolve = 'Resolve-CommitScope'; Label = 'scope' }
    )) {
        if (-not $check.Value) { continue }
        try { & $check.Resolve -Value $check.Value | Out-Null }
        catch { $violations.Add("Invalid commit $($check.Label): $($_.Exception.Message)") }
    }
    return $violations
}

function Confirm-OptionsAreConsistent {
    param([Parameter(Mandatory)]$Options)
    $violations = @(Get-OptionViolations -Options $Options)
    if ($violations.Count -eq 0) { return }

    throw (New-ToolException -Message 'Invalid option combination.' -Hint 'Run "Get-Help .\gh-tool.ps1 -Examples" for usage.' -Details @($violations))
}

# ----------------------------------------------------------------------------
# Conventional commit subject
# ----------------------------------------------------------------------------
function Resolve-CommitType {
    param([Parameter(Mandatory)][string]$Value)

    $token = $Value.Trim().TrimEnd('!').Trim().ToLowerInvariant()
    if (-not $token) {
        throw (New-ToolException -Message 'The commit type is empty.' -Hint "Valid types: $($CONVENTIONAL_COMMIT_TYPES -join ', ').")
    }
    if ($CONVENTIONAL_COMMIT_TYPES -contains $token) { return $token }
    if ($COMMIT_TYPE_SHORTCUTS.Contains($token)) { return [string]$COMMIT_TYPE_SHORTCUTS[$token] }

    $candidates = [System.Collections.Generic.SortedSet[string]]::new([System.StringComparer]::Ordinal)
    foreach ($key in $COMMIT_TYPE_SHORTCUTS.Keys) {
        if ($key.StartsWith($token)) { [void]$candidates.Add([string]$COMMIT_TYPE_SHORTCUTS[$key]) }
    }
    foreach ($type in $CONVENTIONAL_COMMIT_TYPES) {
        if ($type.StartsWith($token)) { [void]$candidates.Add($type) }
    }

    if ($candidates.Count -eq 1) { return @($candidates)[0] }
    $validTypes = "Valid types: $($CONVENTIONAL_COMMIT_TYPES -join ', ')."
    if ($candidates.Count -eq 0) {
        throw (New-ToolException -Message "'$Value' is not a commit type." -Hint $validTypes)
    }
    throw (New-ToolException -Message "'$Value' is an ambiguous commit type." -Hint "It matches $($candidates -join ', '). Spell it out. $validTypes")
}

function Resolve-CommitScope {
    param([Parameter(Mandatory)][string]$Value)

    $token = $Value.Trim().Trim('(', ')').Trim().ToLowerInvariant()
    if (-not $token) {
        throw (New-ToolException -Message 'The commit scope is empty.' -Hint 'Pass a scope such as u (ui) or api, or drop -Scope.')
    }
    if ($COMMIT_SCOPE_SHORTCUTS.Contains($token)) { return [string]$COMMIT_SCOPE_SHORTCUTS[$token] }
    if ($token -match $COMMIT_SCOPE_PATTERN) { return $token }
    throw (New-ToolException -Message "'$Value' is not a valid commit scope." -Hint 'Use lowercase letters, digits, dots, dashes, underscores, or slashes: ui, api/v2.')
}

function Set-ConventionalCommitMessage {
    param([Parameter(Mandatory)]$Options)
    if (-not $Options.Message -or [string]::IsNullOrWhiteSpace($Options.Message)) { return }

    $lines = $Options.Message -split "`r?`n"
    $headline = $lines[0]
    $body = if ($lines.Count -gt 1) { ($lines[1..($lines.Count - 1)]) -join "`n" } else { '' }

    $resolvedType = ''
    $resolvedScope = ''
    $isBreaking = $false
    $alreadyConventional = $false
    $subject = $headline.Trim()

    if ($headline -match $CONVENTIONAL_SUBJECT_PATTERN) {
        try {
            $resolvedType = Resolve-CommitType -Value $Matches['type']
            $alreadyConventional = $true
        }
        catch {
            $resolvedType = ''
        }
        if ($alreadyConventional) {
            if ($Matches['scope']) { $resolvedScope = Resolve-CommitScope -Value $Matches['scope'] }
            $isBreaking = [bool]$Matches['breaking']
            $subject = $Matches['subject'].Trim()
        }
    }

    if ($Options.Type) {
        $requestedType = Resolve-CommitType -Value $Options.Type
        if ($alreadyConventional -and $requestedType -ne $resolvedType) {
            throw (New-ToolException -Message "The message starts with '$resolvedType' but -Type resolves to '$requestedType'." -Hint 'Drop -Type, or change it so both agree.')
        }
        $resolvedType = $requestedType
        if ($Options.Type.Trim().EndsWith('!')) { $isBreaking = $true }
    }

    if ($Options.Scope) {
        $requestedScope = Resolve-CommitScope -Value $Options.Scope
        if ($alreadyConventional -and $resolvedScope -and $requestedScope -ne $resolvedScope) {
            throw (New-ToolException -Message "The message scope is '$resolvedScope' but -Scope resolves to '$requestedScope'." -Hint 'Drop -Scope, or change it so both agree.')
        }
        $resolvedScope = $requestedScope
    }

    if (-not $resolvedType) {
        if ($Options.Scope) {
            throw (New-ToolException -Message '-Scope needs a commit type.' -Hint 'Pass -Type, or start the message with a conventional prefix such as "feat: ...".')
        }
        return
    }

    $composed = $resolvedType
    if ($resolvedScope) { $composed += "($resolvedScope)" }
    if ($isBreaking) { $composed += '!' }
    $composed += ": $subject"

    $composedMessage = if ($body) { "$composed`n$body" } else { $composed }
    Write-LogDebug "Commit subject: $composed"
    $Options.Message = $composedMessage
}

# ----------------------------------------------------------------------------
# Context: environment, repository, token
# ----------------------------------------------------------------------------
function New-ToolContext {
    param(
        [Parameter(Mandatory)]$Options,
        [Parameter(Mandatory)]$Report
    )
    Enter-Step -Report $Report -Name 'Validate environment'
    Initialize-ProcessSettings
    Confirm-GitIsInstalled

    $repository = Get-RepositoryContext
    $token = Get-GitHubToken -Repository $repository -IsDryRun $Options.DryRun
    $targetBranch = if ($Options.Branch) { $Options.Branch } else { $repository.CurrentBranch }
    $context = [pscustomobject]@{
        Options      = $Options
        Repository   = $repository
        Token        = $token
        Report       = $Report
        TargetBranch = $targetBranch
        CommitHash   = $null
    }

    if (-not $Options.Status) { Confirm-WorkflowPreconditions -Context $context }
    if ((Test-GitHubApiRequired -Options $Options) -and -not $Options.DryRun) { Confirm-TokenIsAccepted -Context $context }

    $tokenSource = if ($token) { "token from $($token.Source)" } else { 'no token (dry run)' }
    $detail = "$($repository.Owner)/$($repository.Name) @ $($repository.DisplayBranch), $tokenSource"
    Write-StepResult -Report $Report -Name 'Validate environment' -Outcome 'Success' -Detail $detail
    return $context
}

function Initialize-ProcessSettings {
    [System.Net.ServicePointManager]::SecurityProtocol = [System.Net.ServicePointManager]::SecurityProtocol -bor [System.Net.SecurityProtocolType]::Tls12
    $env:GIT_TERMINAL_PROMPT = '0'
    try { [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false) }
    catch { Write-LogDebug "Console encoding unchanged: $($_.Exception.Message)" }
}

function Confirm-GitIsInstalled {
    if (Get-Command git -ErrorAction SilentlyContinue) {
        Write-LogDebug (Get-GitOutputOrNull -Arguments @('--version'))
        return
    }
    throw (New-ToolException -Message 'Git is not installed or not on PATH.' -Hint 'Install from https://git-scm.com/downloads (Windows: winget install --id Git.Git -e), then reopen the terminal.')
}

function Get-RepositoryContext {
    $insideWorkTree = Get-GitOutputOrNull -Arguments @('rev-parse', '--is-inside-work-tree')
    if ($insideWorkTree -ne 'true') {
        throw (New-ToolException -Message 'The current directory is not inside a Git working tree.' -Hint 'Change into a repository folder, or create one with "git init".')
    }

    $root = Get-GitOutputOrNull -Arguments @('rev-parse', '--show-toplevel')
    $remoteName = Get-RemoteName -Root $root
    $remoteUrl = Get-GitOutputOrNull -Arguments @('config', '--get', "remote.$remoteName.url") -WorkingDirectory $root
    $coordinates = ConvertFrom-GitHubRemoteUrl -Url $remoteUrl
    $currentBranch = Get-GitOutputOrNull -Arguments @('symbolic-ref', '--short', '-q', 'HEAD') -WorkingDirectory $root
    $displayBranch = if ($currentBranch) { $currentBranch } else { 'HEAD (detached)' }

    return [pscustomobject]@{
        Root          = $root
        Owner         = $coordinates.Owner
        Name          = $coordinates.Name
        RemoteName    = $remoteName
        RemoteUrl     = $remoteUrl
        CurrentBranch = $currentBranch
        DisplayBranch = $displayBranch
        DefaultBranch = Get-DefaultBranchName -Root $root -RemoteName $remoteName
    }
}

function Get-RemoteName {
    param([Parameter(Mandatory)][string]$Root)
    $result = Invoke-GitCommand -Arguments @('remote') -WorkingDirectory $Root
    Confirm-GitSucceeded -Result $result -Operation 'Listing remotes'

    if ($result.Output -contains $DEFAULT_REMOTE_NAME) { return $DEFAULT_REMOTE_NAME }
    if ($result.Output.Count -eq 1) { return $result.Output[0] }
    throw (New-ToolException -Message "No remote named '$DEFAULT_REMOTE_NAME' is configured." -Hint "Add one: git remote add $DEFAULT_REMOTE_NAME https://github.com/OWNER/REPO.git")
}

function ConvertFrom-GitHubRemoteUrl {
    param([Parameter(Mandatory)][string]$Url)
    $pattern = '^(?:https?://(?:[^@/]+@)?|ssh://(?:[^@/]+@)?|git@)github\.com[:/]+(?<owner>[^/]+)/(?<repo>[^/]+?)(?:\.git)?/?$'
    $match = [regex]::Match($Url, $pattern)
    if (-not $match.Success) {
        throw (New-ToolException -Message "The remote URL is not a github.com repository: $Url" -Hint 'This tool supports github.com HTTPS and SSH remotes.')
    }
    return @{ Owner = $match.Groups['owner'].Value; Name = $match.Groups['repo'].Value }
}

function Get-DefaultBranchName {
    param(
        [Parameter(Mandatory)][string]$Root,
        [Parameter(Mandatory)][string]$RemoteName
    )
    $symbolicHead = Get-GitOutputOrNull -Arguments @('symbolic-ref', '--short', "refs/remotes/$RemoteName/HEAD") -WorkingDirectory $Root
    if ($symbolicHead) { return $symbolicHead -replace ('^' + [regex]::Escape($RemoteName) + '/'), '' }

    $probe = Invoke-GitCommand -Arguments ($GIT_REMOTE_PROBE_OPTIONS + @('ls-remote', '--symref', $RemoteName, 'HEAD')) -WorkingDirectory $Root
    $match = [regex]::Match(($probe.Output -join "`n"), 'ref:\s+refs/heads/(?<branch>\S+)\s+HEAD')
    if ($probe.ExitCode -eq 0 -and $match.Success) { return $match.Groups['branch'].Value }

    Write-LogWarning "Default branch could not be detected. Assuming '$FALLBACK_DEFAULT_BRANCH'."
    return $FALLBACK_DEFAULT_BRANCH
}

function Get-GitHubToken {
    param(
        [Parameter(Mandatory)]$Repository,
        [Parameter(Mandatory)][bool]$IsDryRun
    )
    $searchDirectories = @((Get-Location).ProviderPath, $Repository.Root) |
        ForEach-Object { [System.IO.Path]::GetFullPath($_) } |
        Select-Object -Unique

    foreach ($directory in $searchDirectories) {
        $envFilePath = Join-Path $directory $ENV_FILE_NAME
        if (-not (Test-Path -LiteralPath $envFilePath -PathType Leaf)) { continue }

        $tokenValue = Read-TokenFromEnvFile -EnvFilePath $envFilePath
        if (-not $tokenValue) { continue }

        Write-LogDebug "Token loaded from $envFilePath"
        Write-WarningIfEnvFileIsNotIgnored -EnvFilePath $envFilePath -Repository $Repository
        return [pscustomobject]@{ Value = $tokenValue; Source = $envFilePath }
    }

    $environmentToken = [System.Environment]::GetEnvironmentVariable($TOKEN_VARIABLE_NAME)
    if ($environmentToken) {
        return [pscustomobject]@{ Value = $environmentToken; Source = "`$env:$TOKEN_VARIABLE_NAME" }
    }

    if ($IsDryRun) {
        Write-LogWarning "$TOKEN_VARIABLE_NAME not found. Continuing because -DryRun sends no API requests."
        return $null
    }
    throw (New-ToolException -Message "$TOKEN_VARIABLE_NAME was not found in a .env file or the environment." -Hint 'Provide a token using one of the options below.' -Details @(
            'Create a token at https://github.com/settings/tokens (classic: repo scope).',
            "Add this line to .env in the repository root: $TOKEN_VARIABLE_NAME=ghp_yourTokenHere",
            "Or set it for this session: `$env:$TOKEN_VARIABLE_NAME = 'ghp_yourTokenHere'",
            'Add .env to .gitignore so the token is never committed.'))
}

function Read-TokenFromEnvFile {
    param([Parameter(Mandatory)][string]$EnvFilePath)
    $linePattern = "^\s*(?:export\s+)?$TOKEN_VARIABLE_NAME\s*=\s*(?<value>.*?)\s*$"
    foreach ($line in Get-Content -LiteralPath $EnvFilePath) {
        $match = [regex]::Match($line, $linePattern)
        if (-not $match.Success) { continue }

        $value = $match.Groups['value'].Value
        $quoted = [regex]::Match($value, '^(?<quote>["''])(?<inner>.*)\k<quote>$')
        $value = if ($quoted.Success) { $quoted.Groups['inner'].Value } else { $value -replace '\s+#.*$', '' }
        if ($value) { return $value }
    }
    return $null
}

function Write-WarningIfEnvFileIsNotIgnored {
    param(
        [Parameter(Mandatory)][string]$EnvFilePath,
        [Parameter(Mandatory)]$Repository
    )
    $normalizedRoot = [System.IO.Path]::GetFullPath($Repository.Root)
    if (-not $EnvFilePath.StartsWith($normalizedRoot, [System.StringComparison]::OrdinalIgnoreCase)) { return }

    $result = Invoke-GitCommand -Arguments @('check-ignore', '-q', '--', $EnvFilePath) -WorkingDirectory $Repository.Root
    if ($result.ExitCode -eq 1) {
        Write-LogWarning "$ENV_FILE_NAME is not git-ignored. Add it to .gitignore so the token cannot be committed."
    }
}

function Confirm-TokenIsAccepted {
    param([Parameter(Mandatory)]$Context)
    $user = Invoke-GitHubApi -Method 'Get' -Endpoint 'user' -Token $Context.Token.Value -Operation 'Authenticating with GitHub'
    Write-LogDebug "Authenticated as $($user.login)"
}

function Confirm-WorkflowPreconditions {
    param([Parameter(Mandatory)]$Context)
    $options = $Context.Options
    $repository = $Context.Repository
    $targetBranch = $Context.TargetBranch

    if ($options.Branch) { Confirm-RefNameIsValid -Arguments @('check-ref-format', '--branch', $options.Branch) -Description "branch name '$($options.Branch)'" -Context $Context }
    if ($options.Tag) { Confirm-RefNameIsValid -Arguments @('check-ref-format', "refs/tags/$($options.Tag)") -Description "tag name '$($options.Tag)'" -Context $Context }

    if ((Test-ShouldPushBranch -Options $options) -and -not $targetBranch) {
        throw (New-ToolException -Message 'HEAD is detached, so there is no branch to push.' -Hint 'Create one with -Branch <name>.')
    }
    if ($options.Pr -and $targetBranch -eq $repository.DefaultBranch) {
        throw (New-ToolException -Message "Cannot open a pull request from the default branch '$targetBranch'." -Hint 'Use -Branch <name> to work on a separate branch.')
    }
    $tagExists = $options.Tag -and (Test-GitRefExists -Ref "refs/tags/$($options.Tag)" -Root $repository.Root)
    if ($tagExists -and -not $options.Release) {
        throw (New-ToolException -Message "Tag '$($options.Tag)' already exists." -Hint "Choose a new tag name, or delete the old one with: git tag -d $($options.Tag)")
    }
}

function Confirm-RefNameIsValid {
    param(
        [Parameter(Mandatory)][string[]]$Arguments,
        [Parameter(Mandatory)][string]$Description,
        [Parameter(Mandatory)]$Context
    )
    $result = Invoke-GitCommand -Arguments $Arguments -WorkingDirectory $Context.Repository.Root
    if ($result.ExitCode -eq 0) { return }
    throw (New-ToolException -Message "Invalid $Description." -Hint 'Names cannot contain spaces, "~", "^", ":", "?", "*", "[", "\", "..", or end with ".lock" or "/".')
}

# ----------------------------------------------------------------------------
# Workflow steps
# ----------------------------------------------------------------------------
function Invoke-BranchSwitch {
    param([Parameter(Mandatory)]$Context)
    $stepName = 'Switch branch'
    Enter-Step -Report $Context.Report -Name $stepName

    $branch = $Context.Options.Branch
    $repository = $Context.Repository
    if ($branch -eq $repository.CurrentBranch) {
        Write-StepResult -Report $Context.Report -Name $stepName -Outcome 'Success' -Detail "already on '$branch'"
        return
    }

    $existsLocally = Test-GitRefExists -Ref "refs/heads/$branch" -Root $repository.Root
    $existsOnRemote = Test-GitRefExists -Ref "refs/remotes/$($repository.RemoteName)/$branch" -Root $repository.Root
    $isExistingBranch = $existsLocally -or $existsOnRemote
    $arguments = if ($isExistingBranch) { @('checkout', $branch) } else { @('checkout', '-b', $branch) }

    Invoke-GitMutation -Context $Context -Arguments $arguments -Operation "Switching to branch '$branch'" | Out-Null
    $action = if ($isExistingBranch) { 'switched to' } else { 'created' }
    Complete-Step -Context $Context -Name $stepName -Detail "$action '$branch'"
}

function Invoke-Staging {
    param([Parameter(Mandatory)]$Context)
    $stepName = 'Stage changes'
    Enter-Step -Report $Context.Report -Name $stepName

    if ($Context.Options.All) {
        $arguments = @('add', '-A', '--', '.', ":(exclude,glob)**/$ENV_FILE_NAME")
        $detail = "all changes (excluding $ENV_FILE_NAME files)"
    }
    else {
        $resolvedPaths = @($Context.Options.Path | ForEach-Object { $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($_) })
        $arguments = @('add', '--') + $resolvedPaths
        $detail = "$($resolvedPaths.Count) path(s)"
    }
    Invoke-GitMutation -Context $Context -Arguments $arguments -Operation 'Staging changes' | Out-Null
    Complete-Step -Context $Context -Name $stepName -Detail $detail
}

function Test-HasStagedChanges {
    param([Parameter(Mandatory)]$Context)
    $result = Invoke-GitCommand -Arguments @('diff', '--cached', '--quiet') -WorkingDirectory $Context.Repository.Root
    if ($result.ExitCode -eq 0) { return $false }
    if ($result.ExitCode -eq 1) { return $true }
    Confirm-GitSucceeded -Result $result -Operation 'Checking staged changes'
}

function Invoke-Commit {
    param([Parameter(Mandatory)]$Context)
    $stepName = 'Commit'
    Enter-Step -Report $Context.Report -Name $stepName

    $hasStagedChanges = Test-HasStagedChanges -Context $Context
    if (-not $hasStagedChanges -and -not $Context.Options.DryRun) {
        Write-StepResult -Report $Context.Report -Name $stepName -Outcome 'Skipped' -Detail 'nothing staged (use -All or -Path to stage files)'
        return
    }

    Invoke-GitMutation -Context $Context -Arguments @('commit', '-m', $Context.Options.Message) -Operation 'Committing' | Out-Null
    if ($Context.Options.DryRun) {
        Complete-Step -Context $Context -Name $stepName -Detail "message: $($Context.Options.Message)"
        return
    }
    $Context.CommitHash = Get-GitOutputOrNull -Arguments @('rev-parse', '--short', 'HEAD') -WorkingDirectory $Context.Repository.Root
    Complete-Step -Context $Context -Name $stepName -Detail $Context.CommitHash
}

function Invoke-BranchPush {
    param([Parameter(Mandatory)]$Context)
    $stepName = 'Push branch'
    Enter-Step -Report $Context.Report -Name $stepName

    $repository = $Context.Repository
    $branch = $Context.TargetBranch
    if ($Context.Options.Force -and $branch -eq $repository.DefaultBranch) {
        Write-LogWarning "Force pushing to the default branch '$branch'."
    }

    $upstreamQuery = '{0}@{{u}}' -f $branch
    $hasUpstream = [bool](Get-GitOutputOrNull -Arguments @('rev-parse', '--abbrev-ref', $upstreamQuery) -WorkingDirectory $repository.Root)
    $arguments = $GIT_NETWORK_OPTIONS + @('push')
    if ($Context.Options.Force) { $arguments += '--force-with-lease' }
    if (-not $hasUpstream) { $arguments += '--set-upstream' }
    $arguments += @($repository.RemoteName, $branch)

    Invoke-GitMutation -Context $Context -Arguments $arguments -Operation "Pushing '$branch'" | Out-Null
    if (-not $Context.Options.DryRun) {
        Add-ReportLink -Report $Context.Report -Label 'Branch' -Url "$GITHUB_WEB_BASE_URL/$($repository.Owner)/$($repository.Name)/tree/$branch"
    }
    Complete-Step -Context $Context -Name $stepName -Detail "$($repository.RemoteName)/$branch"
}

function Invoke-TagCreation {
    param([Parameter(Mandatory)]$Context)
    $stepName = 'Create tag'
    Enter-Step -Report $Context.Report -Name $stepName

    $tag = $Context.Options.Tag
    if (Test-GitRefExists -Ref "refs/tags/$tag" -Root $Context.Repository.Root) {
        Write-StepResult -Report $Context.Report -Name $stepName -Outcome 'Skipped' -Detail "'$tag' already exists, reusing it for the release"
        return
    }

    $tagMessage = if ($Context.Options.Message) { $Context.Options.Message } else { "Release $tag" }
    Invoke-GitMutation -Context $Context -Arguments @('tag', '-a', $tag, '-m', $tagMessage) -Operation "Creating tag '$tag'" | Out-Null
    Complete-Step -Context $Context -Name $stepName -Detail $tag
}

function Invoke-TagPush {
    param([Parameter(Mandatory)]$Context)
    $stepName = 'Push tag'
    Enter-Step -Report $Context.Report -Name $stepName

    $tag = $Context.Options.Tag
    $arguments = $GIT_NETWORK_OPTIONS + @('push', $Context.Repository.RemoteName, "refs/tags/$tag")
    Invoke-GitMutation -Context $Context -Arguments $arguments -Operation "Pushing tag '$tag'" | Out-Null
    Complete-Step -Context $Context -Name $stepName -Detail $tag
}

function Invoke-PullRequestCreation {
    param([Parameter(Mandatory)]$Context)
    $stepName = 'Create pull request'
    Enter-Step -Report $Context.Report -Name $stepName

    $repository = $Context.Repository
    $head = $Context.TargetBranch
    $base = $repository.DefaultBranch

    $existingPullRequest = Find-OpenPullRequest -Context $Context
    if ($existingPullRequest) {
        Add-ReportLink -Report $Context.Report -Label 'Pull Request' -Url $existingPullRequest.html_url
        Write-StepResult -Report $Context.Report -Name $stepName -Outcome 'Skipped' -Detail "#$($existingPullRequest.number) is already open for '$head'"
        if ($Context.Options.AutoMerge) { Invoke-AutoMergeRequest -Context $Context -PullRequestNumber ([int]$existingPullRequest.number) }
        return
    }

    $requestBody = @{
        title = Resolve-PullRequestTitle -Context $Context
        head  = $head
        base  = $base
        body  = Resolve-PullRequestBody -Context $Context
        draft = [bool]$Context.Options.Draft
    }
    $endpoint = "repos/$($repository.Owner)/$($repository.Name)/pulls"
    $response = Invoke-GitHubMutation -Context $Context -Method 'Post' -Endpoint $endpoint -Body $requestBody -Operation 'Creating pull request'
    if ($response) { Add-ReportLink -Report $Context.Report -Label 'Pull Request' -Url $response.html_url }
    Complete-Step -Context $Context -Name $stepName -Detail "$head -> $base"

    if ($Context.Options.AutoMerge) {
        $number = if ($response) { [int]$response.number } else { 0 }
        Invoke-AutoMergeRequest -Context $Context -PullRequestNumber $number
    }
}

function Invoke-AutoMergeRequest {
    param(
        [Parameter(Mandatory)]$Context,
        [int]$PullRequestNumber = 0
    )
    $stepName = 'Enable auto-merge'
    Enter-Step -Report $Context.Report -Name $stepName

    $repository = $Context.Repository
    $target = if ($PullRequestNumber -gt 0) { "$PullRequestNumber" } else { '<number>' }
    $arguments = @('pr', 'merge', $target, '--auto', '--merge', '--repo', "$($repository.Owner)/$($repository.Name)")

    if ($Context.Options.DryRun) {
        Write-LogDryRun ('gh ' + ($arguments -join ' '))
        Complete-Step -Context $Context -Name $stepName -Detail 'auto-merge planned'
        return
    }

    $command = Get-Command gh -CommandType Application -ErrorAction SilentlyContinue
    if (-not $command) {
        Write-LogWarning "GitHub CLI not found. Enable auto-merge manually: gh $($arguments -join ' ')"
        Write-StepResult -Report $Context.Report -Name $stepName -Outcome 'Skipped' -Detail 'gh is not installed'
        return
    }

    $previousToken = $env:GH_TOKEN
    $previousPreference = $ErrorActionPreference
    try {
        if ($Context.Token -and $Context.Token.Value) { $env:GH_TOKEN = $Context.Token.Value }
        $ErrorActionPreference = 'Continue'
        Write-LogDebug ('gh ' + ($arguments -join ' '))
        $lines = @(& $command.Source @arguments 2>&1 | ForEach-Object { "$_" })
        $exitCode = $LASTEXITCODE
    }
    finally {
        $env:GH_TOKEN = $previousToken
        $ErrorActionPreference = $previousPreference
    }

    if ($exitCode -ne 0) {
        $detail = @($lines | Where-Object { $_ } | Select-Object -Last $MAX_ERROR_DETAIL_LINES) -join ' '
        Write-LogWarning "Auto-merge was not enabled: $detail"
        Write-StepResult -Report $Context.Report -Name $stepName -Outcome 'Skipped' -Detail ($detail.Trim())
        return
    }
    Complete-Step -Context $Context -Name $stepName -Detail "#$PullRequestNumber merges when required checks pass"
}

function Find-OpenPullRequest {
    param([Parameter(Mandatory)]$Context)
    if ($Context.Options.DryRun) { return $null }

    $repository = $Context.Repository
    $headFilter = [uri]::EscapeDataString("$($repository.Owner):$($Context.TargetBranch)")
    $endpoint = "repos/$($repository.Owner)/$($repository.Name)/pulls?state=open&head=$headFilter"
    $openPullRequests = @(Invoke-GitHubApi -Method 'Get' -Endpoint $endpoint -Token $Context.Token.Value -Operation 'Looking for an existing pull request')
    return $openPullRequests | Select-Object -First 1
}

function Resolve-PullRequestTitle {
    param([Parameter(Mandatory)]$Context)
    $options = $Context.Options
    if ($options.PrTitle) { return $options.PrTitle }
    if ($options.DryRun -and $options.Message) { return ($options.Message -split "`r?`n")[0] }

    $subject = Get-GitOutputOrNull -Arguments @('log', '-1', '--pretty=format:%s') -WorkingDirectory $Context.Repository.Root
    if ($subject) { return $subject }
    throw (New-ToolException -Message 'No pull request title available.' -Hint 'The repository has no commits. Pass -PrTitle.')
}

function Resolve-PullRequestBody {
    param([Parameter(Mandatory)]$Context)
    $options = $Context.Options
    if ($options.PrBody) { return $options.PrBody }
    if ($options.DryRun -and $options.Message) { return $options.Message }

    $result = Invoke-GitCommand -Arguments @('log', '-1', '--pretty=format:%B') -WorkingDirectory $Context.Repository.Root
    return ($result.Output -join "`n").Trim()
}

function Invoke-IssueCreation {
    param([Parameter(Mandatory)]$Context)
    $stepName = 'Create issue'
    Enter-Step -Report $Context.Report -Name $stepName

    $repository = $Context.Repository
    $requestBody = @{ title = $Context.Options.IssueTitle; body = [string]$Context.Options.IssueBody }
    $endpoint = "repos/$($repository.Owner)/$($repository.Name)/issues"
    $response = Invoke-GitHubMutation -Context $Context -Method 'Post' -Endpoint $endpoint -Body $requestBody -Operation 'Creating issue'
    if ($response) { Add-ReportLink -Report $Context.Report -Label 'Issue' -Url $response.html_url }
    Complete-Step -Context $Context -Name $stepName -Detail $Context.Options.IssueTitle
}

function Invoke-ReleaseCreation {
    param([Parameter(Mandatory)]$Context)
    $stepName = 'Create release'
    Enter-Step -Report $Context.Report -Name $stepName

    $repository = $Context.Repository
    $tag = $Context.Options.Tag
    $requestBody = @{ tag_name = $tag; name = $tag; generate_release_notes = $true; draft = $false; prerelease = $false }
    $endpoint = "repos/$($repository.Owner)/$($repository.Name)/releases"
    $response = Invoke-GitHubMutation -Context $Context -Method 'Post' -Endpoint $endpoint -Body $requestBody -Operation 'Creating release'
    if ($response) { Add-ReportLink -Report $Context.Report -Label 'Release' -Url $response.html_url }
    Complete-Step -Context $Context -Name $stepName -Detail $tag
}

# ----------------------------------------------------------------------------
# Status
# ----------------------------------------------------------------------------
function Show-RepositoryStatus {
    param([Parameter(Mandatory)]$Context)
    $stepName = 'Collect status'
    Enter-Step -Report $Context.Report -Name $stepName

    $repository = $Context.Repository
    $rows = @(
        @{ Label = 'Repository'; Value = "$($repository.Owner)/$($repository.Name)" }
        @{ Label = 'Remote'; Value = $repository.RemoteUrl }
        @{ Label = 'Branch'; Value = "$($repository.DisplayBranch) (default: $($repository.DefaultBranch))" }
        @{ Label = 'Working tree'; Value = Get-WorkingTreeSummary -Repository $repository }
        @{ Label = 'Sync'; Value = Get-SyncSummary -Repository $repository }
    )
    $pullRequests = @(Get-OpenPullRequestsForStatus -Context $Context)

    Write-Host ''
    Write-SectionTitle 'Repository Status'
    foreach ($row in $rows) {
        Write-TreeLine -Text ('{0,-13}{1}' -f $row.Label, $row.Value) -IsLast $false
    }
    Write-PullRequestRows -Context $Context -PullRequests $pullRequests
    Write-StepResult -Report $Context.Report -Name $stepName -Outcome 'Success' -Detail "$($repository.Owner)/$($repository.Name)"
}

function Get-WorkingTreeSummary {
    param([Parameter(Mandatory)]$Repository)
    $result = Invoke-GitCommand -Arguments @('status', '--porcelain=v1') -WorkingDirectory $Repository.Root
    Confirm-GitSucceeded -Result $result -Operation 'Reading working tree status'

    $stagedCount = 0
    $modifiedCount = 0
    $untrackedCount = 0
    foreach ($line in $result.Output) {
        if ($line.StartsWith('??')) { $untrackedCount++; continue }
        if ($line[0] -ne ' ') { $stagedCount++ }
        if ($line[1] -ne ' ') { $modifiedCount++ }
    }
    if ($stagedCount + $modifiedCount + $untrackedCount -eq 0) { return 'clean' }
    return "$stagedCount staged, $modifiedCount modified, $untrackedCount untracked"
}

function Get-SyncSummary {
    param([Parameter(Mandatory)]$Repository)
    $upstream = Get-GitOutputOrNull -Arguments @('rev-parse', '--abbrev-ref', '@{u}') -WorkingDirectory $Repository.Root
    if (-not $upstream) { return 'no upstream configured' }

    $counts = Get-GitOutputOrNull -Arguments @('rev-list', '--left-right', '--count', '@{u}...HEAD') -WorkingDirectory $Repository.Root
    $fields = $counts -split '\s+'
    return "ahead $($fields[1]), behind $($fields[0]) of $upstream (as of last fetch)"
}

function Get-OpenPullRequestsForStatus {
    param([Parameter(Mandatory)]$Context)
    if ($Context.Options.DryRun) { return }
    $repository = $Context.Repository
    $endpoint = "repos/$($repository.Owner)/$($repository.Name)/pulls?state=open&per_page=$MAX_LISTED_PULL_REQUESTS"
    return Invoke-GitHubApi -Method 'Get' -Endpoint $endpoint -Token $Context.Token.Value -Operation 'Listing open pull requests'
}

function Write-PullRequestRows {
    param(
        [Parameter(Mandatory)]$Context,
        [AllowNull()][object[]]$PullRequests
    )
    if ($Context.Options.DryRun) {
        Write-TreeLine -Text 'Open PRs     skipped (dry run sends no API requests)' -IsLast $true -Color DarkGray
        return
    }
    $countLabel = if ($PullRequests.Count -ge $MAX_LISTED_PULL_REQUESTS) { "$MAX_LISTED_PULL_REQUESTS+" } else { "$($PullRequests.Count)" }
    Write-TreeLine -Text "Open PRs     $countLabel" -IsLast $true
    foreach ($pullRequest in $PullRequests) {
        $line = '#{0} {1} ({2} -> {3})  {4}' -f $pullRequest.number, $pullRequest.title, $pullRequest.head.ref, $pullRequest.base.ref, $pullRequest.html_url
        Write-Host ($TREE_INDENT + '    ' + $line) -ForegroundColor Gray
    }
}

# ----------------------------------------------------------------------------
# Execution report and summary
# ----------------------------------------------------------------------------
function New-ExecutionReport {
    return [pscustomobject]@{
        Steps       = [System.Collections.Generic.List[object]]::new()
        Links       = [System.Collections.Generic.List[object]]::new()
        CurrentStep = 'Initialize'
        ExitCode    = 0
    }
}

function Enter-Step {
    param(
        [Parameter(Mandatory)]$Report,
        [Parameter(Mandatory)][string]$Name
    )
    $Report.CurrentStep = $Name
    Write-LogInfo "$Name..."
}

function Add-ReportStep {
    param(
        [Parameter(Mandatory)]$Report,
        [Parameter(Mandatory)][string]$Name,
        [Parameter(Mandatory)][ValidateSet('Success', 'Skipped', 'Failed', 'Planned')][string]$Outcome,
        [string]$Detail = ''
    )
    $Report.Steps.Add([pscustomobject]@{ Name = $Name; Outcome = $Outcome; Detail = $Detail })
}

function Add-ReportLink {
    param(
        [Parameter(Mandatory)]$Report,
        [Parameter(Mandatory)][string]$Label,
        [Parameter(Mandatory)][string]$Url
    )
    $Report.Links.Add([pscustomobject]@{ Label = $Label; Url = $Url })
}

function Write-StepResult {
    param(
        [Parameter(Mandatory)]$Report,
        [Parameter(Mandatory)][string]$Name,
        [Parameter(Mandatory)][ValidateSet('Success', 'Skipped', 'Planned')][string]$Outcome,
        [string]$Detail = ''
    )
    Add-ReportStep -Report $Report -Name $Name -Outcome $Outcome -Detail $Detail
    $text = if ($Detail) { "$Name - $Detail" } else { $Name }
    switch ($Outcome) {
        'Success' { Write-LogSuccess $text }
        'Skipped' { Write-LogWarning $text }
        'Planned' { Write-LogDryRun $text }
    }
}

function Complete-Step {
    param(
        [Parameter(Mandatory)]$Context,
        [Parameter(Mandatory)][string]$Name,
        [string]$Detail = ''
    )
    $outcome = if ($Context.Options.DryRun) { 'Planned' } else { 'Success' }
    Write-StepResult -Report $Context.Report -Name $Name -Outcome $outcome -Detail $Detail
}

function Get-OutcomeStyle {
    param([Parameter(Mandatory)][string]$Outcome)
    switch ($Outcome) {
        'Success' { return @{ Glyph = $GLYPH_SUCCESS; Color = 'Green' } }
        'Failed'  { return @{ Glyph = $GLYPH_FAILURE; Color = 'Red' } }
        'Skipped' { return @{ Glyph = $GLYPH_WARNING; Color = 'Yellow' } }
        'Planned' { return @{ Glyph = $GLYPH_PLANNED; Color = 'Magenta' } }
    }
}

function Get-OverallResultLabel {
    param(
        [Parameter(Mandatory)]$Report,
        [Parameter(Mandatory)]$Options
    )
    $outcomes = @($Report.Steps | ForEach-Object { $_.Outcome })
    if ($Report.ExitCode -ne 0) { return @{ Text = 'FAILED'; Color = 'Red' } }
    if ($Options.DryRun) { return @{ Text = 'DRY RUN COMPLETE (nothing was changed)'; Color = 'Magenta' } }
    if ($outcomes -contains 'Skipped') { return @{ Text = 'COMPLETED WITH WARNINGS'; Color = 'Yellow' } }
    return @{ Text = 'SUCCESS'; Color = 'Green' }
}

function Write-ExecutionSummary {
    param(
        [Parameter(Mandatory)]$Report,
        [Parameter(Mandatory)]$Options,
        [Parameter(Mandatory)][timespan]$Elapsed
    )
    Write-Host ''
    Write-SectionTitle 'Execution Summary'

    $steps = @($Report.Steps)
    for ($index = 0; $index -lt $steps.Count; $index++) {
        $step = $steps[$index]
        $style = Get-OutcomeStyle -Outcome $step.Outcome
        $connector = if ($index -eq $steps.Count - 1) { $GLYPH_LAST } else { $GLYPH_BRANCH }
        Write-Host ($TREE_INDENT + $connector + ' ') -ForegroundColor DarkGray -NoNewline
        Write-Host ("{0} {1,-22}" -f $style.Glyph, $step.Name) -ForegroundColor $style.Color -NoNewline
        Write-Host $step.Detail -ForegroundColor Gray
    }

    $links = @($Report.Links)
    if ($links.Count -gt 0) {
        Write-Host ''
        Write-Host 'Links' -ForegroundColor Cyan
        for ($index = 0; $index -lt $links.Count; $index++) {
            Write-TreeLine -Text ('{0,-13}{1}' -f $links[$index].Label, $links[$index].Url) -IsLast ($index -eq $links.Count - 1) -Color White
        }
    }

    $result = Get-OverallResultLabel -Report $Report -Options $Options
    Write-Host ''
    Write-Host 'Result     ' -NoNewline
    Write-Host $result.Text -ForegroundColor $result.Color
    Write-Host ('Duration   {0:N2}s' -f $Elapsed.TotalSeconds)
}

# ----------------------------------------------------------------------------
# Git execution
# ----------------------------------------------------------------------------
function Invoke-GitCommand {
    param(
        [Parameter(Mandatory)][string[]]$Arguments,
        [string]$WorkingDirectory
    )
    $fullArguments = @(if ($WorkingDirectory) { @('-C', $WorkingDirectory) + $Arguments } else { $Arguments })
    Write-LogDebug ('git ' + ($fullArguments -join ' '))

    $previousPreference = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $lines = & git @fullArguments 2>&1 | ForEach-Object { "$_" }
        $exitCode = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $previousPreference
    }

    $output = @($lines | Where-Object { $_ -ne '' })
    Write-LogDebug "git exited with code $exitCode"
    return [pscustomobject]@{ ExitCode = $exitCode; Output = $output }
}

function Get-GitOutputOrNull {
    param(
        [Parameter(Mandatory)][string[]]$Arguments,
        [string]$WorkingDirectory
    )
    $result = Invoke-GitCommand -Arguments $Arguments -WorkingDirectory $WorkingDirectory
    if ($result.ExitCode -ne 0 -or $result.Output.Count -eq 0) { return $null }
    return $result.Output[0]
}

function Test-GitRefExists {
    param(
        [Parameter(Mandatory)][string]$Ref,
        [Parameter(Mandatory)][string]$Root
    )
    $result = Invoke-GitCommand -Arguments @('show-ref', '--verify', '--quiet', $Ref) -WorkingDirectory $Root
    return $result.ExitCode -eq 0
}

function Get-GitFailureDiagnosis {
    param([string[]]$Output = @())
    $combinedOutput = $Output -join "`n"
    foreach ($candidate in $GIT_FAILURE_PATTERNS) {
        if ($combinedOutput -match $candidate.Pattern) { return $candidate }
    }
    $lastLine = if ($Output.Count -gt 0) { $Output[-1] } else { 'git produced no output' }
    return @{ Summary = $lastLine; Hint = 'Run again with -VerboseLog to see the full git output.' }
}

function Confirm-GitSucceeded {
    param(
        [Parameter(Mandatory)]$Result,
        [Parameter(Mandatory)][string]$Operation
    )
    if ($Result.ExitCode -eq 0) { return }

    $diagnosis = Get-GitFailureDiagnosis -Output $Result.Output
    $details = @($Result.Output | Select-Object -Last $MAX_ERROR_DETAIL_LINES)
    throw (New-ToolException -Message "$Operation failed: $($diagnosis.Summary)" -Hint $diagnosis.Hint -Details $details)
}

function Invoke-GitMutation {
    param(
        [Parameter(Mandatory)]$Context,
        [Parameter(Mandatory)][string[]]$Arguments,
        [Parameter(Mandatory)][string]$Operation
    )
    if ($Context.Options.DryRun) {
        Write-LogDryRun ('git ' + ($Arguments -join ' '))
        return $null
    }
    $result = Invoke-GitCommand -Arguments $Arguments -WorkingDirectory $Context.Repository.Root
    Confirm-GitSucceeded -Result $result -Operation $Operation
    return $result
}

# ----------------------------------------------------------------------------
# GitHub API
# ----------------------------------------------------------------------------
function Invoke-GitHubMutation {
    param(
        [Parameter(Mandatory)]$Context,
        [Parameter(Mandatory)][ValidateSet('Post', 'Patch')][string]$Method,
        [Parameter(Mandatory)][string]$Endpoint,
        [Parameter(Mandatory)][hashtable]$Body,
        [Parameter(Mandatory)][string]$Operation
    )
    if ($Context.Options.DryRun) {
        Write-LogDryRun ('{0} {1}/{2} {3}' -f $Method.ToUpper(), $GITHUB_API_BASE_URL, $Endpoint, ($Body | ConvertTo-Json -Compress))
        return $null
    }
    return Invoke-GitHubApi -Method $Method -Endpoint $Endpoint -Body $Body -Token $Context.Token.Value -Operation $Operation
}

function Invoke-GitHubApi {
    param(
        [Parameter(Mandatory)][ValidateSet('Get', 'Post', 'Patch')][string]$Method,
        [Parameter(Mandatory)][string]$Endpoint,
        [Parameter(Mandatory)][string]$Token,
        [Parameter(Mandatory)][string]$Operation,
        [hashtable]$Body
    )
    $request = @{
        Method      = $Method
        Uri         = "$GITHUB_API_BASE_URL/$Endpoint"
        TimeoutSec  = $GITHUB_API_TIMEOUT_SECONDS
        ErrorAction = 'Stop'
        Headers     = @{
            Authorization          = "Bearer $Token"
            Accept                 = 'application/vnd.github+json'
            'User-Agent'           = $USER_AGENT
            'X-GitHub-Api-Version' = $GITHUB_API_VERSION
        }
    }
    if ($Body) {
        $request.Body = [System.Text.Encoding]::UTF8.GetBytes(($Body | ConvertTo-Json -Depth 5 -Compress))
        $request.ContentType = 'application/json; charset=utf-8'
    }

    Write-LogDebug "$($Method.ToUpper()) $($request.Uri)"
    try {
        return Invoke-RestMethod @request
    }
    catch {
        throw (ConvertTo-GitHubApiException -ErrorRecord $_ -Operation $Operation)
    }
}

function Get-OptionalProperty {
    param(
        [Parameter(Mandatory)]$InputObject,
        [Parameter(Mandatory)][string]$Name
    )
    $property = $InputObject.PSObject.Properties[$Name]
    if ($property) { return $property.Value }
    return $null
}

function Get-HttpStatusCode {
    param([Parameter(Mandatory)][System.Exception]$Exception)
    $response = Get-OptionalProperty -InputObject $Exception -Name 'Response'
    if ($null -eq $response) { return 0 }
    return [int]$response.StatusCode
}

function Get-ApiErrorMessage {
    param([Parameter(Mandatory)][System.Management.Automation.ErrorRecord]$ErrorRecord)
    $rawBody = if ($ErrorRecord.ErrorDetails) { $ErrorRecord.ErrorDetails.Message } else { $null }
    if (-not $rawBody) { return $ErrorRecord.Exception.Message }

    try { $parsed = $rawBody | ConvertFrom-Json }
    catch { return $rawBody }

    $messages = [System.Collections.Generic.List[string]]::new()
    $topLevelMessage = Get-OptionalProperty -InputObject $parsed -Name 'message'
    if ($topLevelMessage) { $messages.Add([string]$topLevelMessage) }
    $apiErrors = @(Get-OptionalProperty -InputObject $parsed -Name 'errors') | Where-Object { $null -ne $_ }
    foreach ($apiError in $apiErrors) {
        if ($apiError -is [string]) { $messages.Add($apiError); continue }
        $errorMessage = Get-OptionalProperty -InputObject $apiError -Name 'message'
        $errorCode = Get-OptionalProperty -InputObject $apiError -Name 'code'
        $errorField = Get-OptionalProperty -InputObject $apiError -Name 'field'
        if ($errorMessage) { $messages.Add([string]$errorMessage) }
        elseif ($errorCode) { $messages.Add("$errorField $errorCode".Trim()) }
    }
    if ($messages.Count -eq 0) { return $rawBody }
    return $messages -join '; '
}

function ConvertTo-GitHubApiException {
    param(
        [Parameter(Mandatory)][System.Management.Automation.ErrorRecord]$ErrorRecord,
        [Parameter(Mandatory)][string]$Operation
    )
    $statusCode = Get-HttpStatusCode -Exception $ErrorRecord.Exception
    $apiMessage = Get-ApiErrorMessage -ErrorRecord $ErrorRecord

    switch ($statusCode) {
        0 {
            $summary = 'Network failure or timeout reaching api.github.com'
            $hint = 'Check the network connection, proxy, and VPN, then retry.'
        }
        401 {
            $summary = "GitHub rejected the token (401): $apiMessage"
            $hint = "The token is invalid, expired, or revoked. Create a new one at https://github.com/settings/tokens and update $TOKEN_VARIABLE_NAME."
        }
        403 {
            $summary = "Forbidden (403): $apiMessage"
            $hint = 'The token lacks permission (classic: repo scope; fine-grained: Contents, Pull requests, Issues write), needs SSO authorization, or the rate limit was reached.'
        }
        404 {
            $summary = "Not found (404): $apiMessage"
            $hint = 'The repository does not exist, or the token cannot see it. Private repositories need a token with access.'
        }
        422 {
            $summary = "Validation failed (422): $apiMessage"
            $hint = 'Check that the branch is pushed and has commits ahead of the base branch, and that the item does not already exist.'
        }
        429 {
            $summary = "Rate limited (429): $apiMessage"
            $hint = 'Wait a few minutes before retrying.'
        }
        default {
            $summary = "HTTP $statusCode`: $apiMessage"
            $hint = 'Retry later. If the problem persists, check https://www.githubstatus.com.'
        }
    }
    return New-ToolException -Message "$Operation failed: $summary" -Hint $hint
}

# ----------------------------------------------------------------------------
# Entry point
# ----------------------------------------------------------------------------
$toolOptions = [pscustomobject]@{
    Message    = $Message
    Type       = $Type
    Scope      = $Scope
    All        = $All.IsPresent
    Path       = [string[]]@($Path | Where-Object { $_ })
    Force      = $Force.IsPresent
    Branch     = $Branch
    Pr         = $Pr.IsPresent
    PrTitle    = $PrTitle
    PrBody     = $PrBody
    Draft      = $Draft.IsPresent
    AutoMerge  = $AutoMerge.IsPresent
    Issue      = $Issue.IsPresent
    IssueTitle = $IssueTitle
    IssueBody  = $IssueBody
    Tag        = $Tag
    Release    = $Release.IsPresent
    Status     = $Status.IsPresent
    DryRun     = $DryRun.IsPresent
}

$executionReport = New-ExecutionReport
Write-Host "$TOOL_NAME $TOOL_VERSION" -ForegroundColor DarkGray
Invoke-GitHubTool -Options $toolOptions -Report $executionReport
exit $executionReport.ExitCode
