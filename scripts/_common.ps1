$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

if ($PSVersionTable.PSVersion.Major -lt 7) {
    throw "ERROR: AGENT//TRACE requires PowerShell 7 or newer for repository-local Windows scripts. No changes were made."
}

$script:ScriptDirectory = $PSScriptRoot
$script:RepoRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))

function Fail {
    param([Parameter(Mandatory = $true)][string]$Message)
    throw "ERROR: $Message"
}

function Assert-RepositoryPath {
    param([Parameter(Mandatory = $true)][string]$Path)
    $fullPath = [System.IO.Path]::GetFullPath($Path)
    $repositoryPrefix = $script:RepoRoot.TrimEnd([System.IO.Path]::DirectorySeparatorChar) + [System.IO.Path]::DirectorySeparatorChar
    if (-not $fullPath.StartsWith($repositoryPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
        Fail "Path must resolve inside the repository: $fullPath"
    }
    return $fullPath
}
