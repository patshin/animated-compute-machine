. (Join-Path $PSScriptRoot "_common.ps1")

$venvRoot = Assert-RepositoryPath (Join-Path $script:RepoRoot ".venv")
$pythonExecutable = Assert-RepositoryPath (Join-Path $venvRoot "Scripts/python.exe")
if (-not (Test-Path -LiteralPath $pythonExecutable -PathType Leaf)) {
    Fail "Missing repository .venv. Run ./scripts/bootstrap.ps1 -Install with Python 3.12/3.13."
}
if (((Get-Item -LiteralPath $venvRoot -Force).Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
    Fail "Invalid .venv: the repository environment must not be a symbolic link or junction. Move it aside and run ./scripts/bootstrap.ps1 -Install."
}

$validation = @'
import importlib.util, os, pathlib, sys
repo = pathlib.Path(os.environ["AGENT_TRACE_EXPECTED_REPO"]).resolve()
expected = (repo / ".venv").resolve()
executable = pathlib.Path(sys.executable).resolve()
prefix = pathlib.Path(sys.prefix).resolve()
if expected not in executable.parents or prefix != expected:
    raise SystemExit(f"Invalid .venv: executable={executable}, prefix={prefix}, expected={expected}. Move it aside and run ./scripts/bootstrap.ps1 -Install.")
if sys.version_info[:2] not in {(3, 12), (3, 13)}:
    raise SystemExit(f"Unsupported Python {sys.version.split()[0]}; use repository .venv with 3.12 or 3.13.")
if importlib.util.find_spec("pip") is None:
    raise SystemExit("Invalid .venv: pip is unavailable. Move it aside and run ./scripts/bootstrap.ps1 -Install.")
'@

$previousExpectedRepo = [Environment]::GetEnvironmentVariable("AGENT_TRACE_EXPECTED_REPO", "Process")
$previousPipCache = [Environment]::GetEnvironmentVariable("PIP_CACHE_DIR", "Process")
try {
    [Environment]::SetEnvironmentVariable("AGENT_TRACE_EXPECTED_REPO", $script:RepoRoot, "Process")
    [Environment]::SetEnvironmentVariable("PIP_CACHE_DIR", (Assert-RepositoryPath (Join-Path $script:RepoRoot ".pip-cache")), "Process")
    & $pythonExecutable -c $validation
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    Push-Location $script:RepoRoot
    try {
        & $pythonExecutable @args
        exit $LASTEXITCODE
    }
    finally {
        Pop-Location
    }
}
finally {
    [Environment]::SetEnvironmentVariable("AGENT_TRACE_EXPECTED_REPO", $previousExpectedRepo, "Process")
    [Environment]::SetEnvironmentVariable("PIP_CACHE_DIR", $previousPipCache, "Process")
}
