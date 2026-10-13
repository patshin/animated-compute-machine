. (Join-Path $PSScriptRoot "_common.ps1")

$nodeCommand = Get-Command node -ErrorAction SilentlyContinue
if ($null -eq $nodeCommand) {
    Fail "Node.js is missing. Use Node 24.15+ LTS or Node 26 without global repository mutations."
}
& $nodeCommand.Source (Join-Path $PSScriptRoot "check-node-version.mjs") (& $nodeCommand.Source --version)
if ($LASTEXITCODE -ne 0) { Fail "Node.js is outside the supported 24.15+ or 26 range." }

& (Join-Path $PSScriptRoot "pnpm.ps1") --version
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& (Join-Path $PSScriptRoot "python.ps1") -c 'import fastapi, jsonschema, langgraph, platform, pydantic, sys, uvicorn; print(f"python={platform.python_version()} executable={sys.executable} prefix={sys.prefix}")'
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
Write-Output "node=$(& $nodeCommand.Source --version)"
Write-Output "repo=$script:RepoRoot"
Write-Output "environment=valid"
