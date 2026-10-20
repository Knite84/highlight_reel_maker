# Verify wrapper commands without starting/stopping services.
$ErrorActionPreference = 'Stop'
foreach ($file in Get-ChildItem -LiteralPath $PSScriptRoot -Filter '*.ps1') {
    $parseErrors = $null
    [System.Management.Automation.Language.Parser]::ParseFile(
        $file.FullName, [ref]$null, [ref]$parseErrors
    ) | Out-Null
    if ($parseErrors.Count) { throw "Parse errors in $($file.Name): $parseErrors" }
}
$global:ReelMakerTestDockerCalls = @()
function docker {
    $global:ReelMakerTestDockerCalls += ,@($args)
    $global:LASTEXITCODE = 0
}
$before = (Get-Location).Path
& (Join-Path $PSScriptRoot 'start-dev.ps1') -SkipBuild
& (Join-Path $PSScriptRoot 'stop-dev.ps1')
if (($global:ReelMakerTestDockerCalls[0] -join ' ') -ne 'compose up -d --wait --wait-timeout 120') {
    throw 'Start must delegate to Compose readiness checking'
}
if (($global:ReelMakerTestDockerCalls[1] -join ' ') -ne 'compose stop --timeout 45') {
    throw 'Stop must delegate to Compose, without native port/process killing'
}
if ((Get-Location).Path -ne $before) { throw 'Wrapper changed the caller working directory' }
Write-Host 'Docker wrapper command, working-directory, and syntax checks passed.'
Remove-Variable -Name ReelMakerTestDockerCalls -Scope Global
