. (Join-Path $PSScriptRoot "_common.ps1")

$apiPort = if ($env:AGENT_TRACE_API_PORT) { $env:AGENT_TRACE_API_PORT } else { "8000" }
$webPort = if ($env:AGENT_TRACE_WEB_PORT) { $env:AGENT_TRACE_WEB_PORT } else { "4173" }
if ($apiPort -notmatch '^\d+$' -or $webPort -notmatch '^\d+$') {
    Fail "AGENT_TRACE_API_PORT and AGENT_TRACE_WEB_PORT must be numeric loopback ports."
}

& (Join-Path $PSScriptRoot "check-env.ps1")
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

$portProbe = 'const net=require("node:net");const s=net.createServer();s.once("error",()=>process.exit(1));s.listen(Number(process.argv[1]),"127.0.0.1",()=>s.close(()=>process.exit(0)));'
foreach ($port in @($apiPort, $webPort)) {
    & node -e $portProbe $port
    if ($LASTEXITCODE -ne 0) { Fail "Loopback port $port is unavailable. Set an unused repository development port." }
}

$pythonExecutable = Assert-RepositoryPath (Join-Path $script:RepoRoot ".venv/Scripts/python.exe")
$startInfo = [System.Diagnostics.ProcessStartInfo]::new()
$startInfo.FileName = $pythonExecutable
$startInfo.UseShellExecute = $false
foreach ($argument in @("-m", "uvicorn", "agent_trace_api.main:app", "--app-dir", (Join-Path $script:RepoRoot "apps/api"), "--host", "127.0.0.1", "--port", $apiPort)) {
    [void]$startInfo.ArgumentList.Add([string]$argument)
}
$apiProcess = [System.Diagnostics.Process]::new()
$apiProcess.StartInfo = $startInfo
if (-not $apiProcess.Start()) { Fail "API process could not be started." }

$previousViteTarget = [Environment]::GetEnvironmentVariable("VITE_API_TARGET", "Process")
try {
    $ready = $false
    for ($attempt = 0; $attempt -lt 100; $attempt += 1) {
        & node -e 'fetch(`http://127.0.0.1:${process.argv[1]}/api/health`).then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1));' $apiPort
        if ($LASTEXITCODE -eq 0) { $ready = $true; break }
        if ($apiProcess.HasExited) { Fail "API process exited before becoming ready." }
        Start-Sleep -Milliseconds 50
    }
    if (-not $ready) { Fail "API did not become ready on the approved loopback port." }
    [Environment]::SetEnvironmentVariable("VITE_API_TARGET", "http://127.0.0.1:$apiPort", "Process")
    & (Join-Path $PSScriptRoot "pnpm.ps1") --filter '@agent-trace/web' exec vite --host 127.0.0.1 --port $webPort --strictPort
    exit $LASTEXITCODE
}
finally {
    [Environment]::SetEnvironmentVariable("VITE_API_TARGET", $previousViteTarget, "Process")
    if (-not $apiProcess.HasExited) {
        $apiProcess.Kill($true)
        [void]$apiProcess.WaitForExit(5000)
    }
    $apiProcess.Dispose()
}
