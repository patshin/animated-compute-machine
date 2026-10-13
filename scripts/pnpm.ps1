. (Join-Path $PSScriptRoot "_common.ps1")

$expectedVersion = "11.16.0"
$invocation = $null

# Corepack chooses packageManager from the working directory. Keep both the
# version probe and the real invocation anchored to this repository.
Push-Location $script:RepoRoot
try {
    $pnpmCommand = Get-Command pnpm -ErrorAction SilentlyContinue
    if ($null -ne $pnpmCommand) {
        $actualVersion = (& $pnpmCommand.Source --version 2>$null)
        if ($LASTEXITCODE -eq 0 -and $actualVersion -eq $expectedVersion) {
            $invocation = @($pnpmCommand.Source)
        }
    }

    if ($null -eq $invocation) {
        $corepackCommand = Get-Command corepack -ErrorAction SilentlyContinue
        if ($null -ne $corepackCommand) {
            $actualVersion = (& $corepackCommand.Source pnpm --version 2>$null)
            if ($LASTEXITCODE -eq 0 -and $actualVersion -eq $expectedVersion) {
                $invocation = @($corepackCommand.Source, "pnpm")
            }
        }
    }

    if ($null -eq $invocation) {
        Fail "pnpm $expectedVersion is required through pnpm or direct corepack pnpm. No global changes were made."
    }

    if ($invocation.Count -eq 1) {
        & $invocation[0] @args
    }
    else {
        & $invocation[0] $invocation[1] @args
    }
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}
