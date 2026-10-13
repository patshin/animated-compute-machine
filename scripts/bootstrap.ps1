param([switch]$Install)

. (Join-Path $PSScriptRoot "_common.ps1")

if (-not $Install) {
    Write-Output "Bootstrap is inspect-first and repo-local."
    Write-Output "Planned writes: .venv/, .pip-cache/, .pnpm-store/, workspace node_modules/, and apps/api/*.egg-info/."
    Write-Output "Network may be used only to resolve pinned lockfile dependencies."
    Write-Output "Run: ./scripts/bootstrap.ps1 -Install"
    exit 0
}

$venvRoot = Assert-RepositoryPath (Join-Path $script:RepoRoot ".venv")
if (Test-Path -LiteralPath $venvRoot) {
    & (Join-Path $PSScriptRoot "python.ps1") -c 'print("existing .venv is valid")'
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}
else {
    $bootstrapPython = $env:AGENT_TRACE_PYTHON
    if ([string]::IsNullOrWhiteSpace($bootstrapPython)) {
        foreach ($candidate in @("python3.13", "python3.12", "python")) {
            $command = Get-Command $candidate -ErrorAction SilentlyContinue
            if ($null -ne $command) {
                $bootstrapPython = $command.Source
                break
            }
        }
    }
    if ([string]::IsNullOrWhiteSpace($bootstrapPython) -or -not (Test-Path -LiteralPath $bootstrapPython -PathType Leaf)) {
        Fail "Python 3.12/3.13 is required. Install it safely or set AGENT_TRACE_PYTHON to an approved interpreter; no global changes were made."
    }
    & $bootstrapPython -c 'import sys; raise SystemExit(0 if sys.version_info[:2] in {(3, 12), (3, 13)} else 2)'
    if ($LASTEXITCODE -ne 0) { Fail "AGENT_TRACE_PYTHON must be Python 3.12 or 3.13." }
    & $bootstrapPython -m venv --copies $venvRoot
    if ($LASTEXITCODE -ne 0) { Fail "Python failed to create the repository .venv." }
}

& (Join-Path $PSScriptRoot "python.ps1") -m pip install --disable-pip-version-check --requirement (Join-Path $script:RepoRoot "requirements.production.lock")
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& (Join-Path $PSScriptRoot "pnpm.ps1") install --frozen-lockfile
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& (Join-Path $PSScriptRoot "check-env.ps1")
exit $LASTEXITCODE
