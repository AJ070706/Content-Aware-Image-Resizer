<#
Keep one verified build after its source commit is pushed. All other immediate
subdirectories of build/ are generated output and may be removed. Use -WhatIf
to inspect the proposed cleanup without deleting anything.
#>
[CmdletBinding(SupportsShouldProcess = $true)]
param([Parameter(Mandatory = $true)][string]$KeepBuild)

$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path -LiteralPath (Split-Path $PSScriptRoot -Parent)).Path
$buildRoot = (Resolve-Path -LiteralPath (Join-Path $repo 'build')).Path
$keep = (Resolve-Path -LiteralPath $KeepBuild).Path

if ([System.IO.Path]::GetDirectoryName($buildRoot) -ne $repo -or
    ((Get-Item -LiteralPath $buildRoot).Attributes -band [System.IO.FileAttributes]::ReparsePoint)) {
    throw 'The build directory must be a real immediate child of this repository.'
}
if ([System.IO.Path]::GetDirectoryName($keep) -ne $buildRoot) {
    throw 'KeepBuild must be an immediate child of this repository build directory.'
}
if ((Get-Item -LiteralPath $keep).Attributes -band [System.IO.FileAttributes]::ReparsePoint) {
    throw 'KeepBuild cannot be a junction or symbolic link.'
}
if (!(Test-Path -LiteralPath (Join-Path $keep 'release\ImageResizer.exe'))) {
    throw 'The build to keep has no packaged executable.'
}
$verification = Join-Path $keep 'verification.log'
$smoke = Join-Path $keep 'smoke-result.json'
if (!(Test-Path -LiteralPath $verification) -or
    (Get-Content -LiteralPath $verification -Raw) -notmatch '(?m)^OK\r?$') {
    throw 'The build to keep has no passing test log.'
}
if (!(Test-Path -LiteralPath $smoke) -or
    !(Get-Content -LiteralPath $smoke -Raw | ConvertFrom-Json).ok) {
    throw 'The build to keep has no passing packaged startup check.'
}

# Retention happens only after the tracked work is committed and pushed.
if (@(git -C $repo status --porcelain).Count -ne 0) {
    throw 'Commit all tracked changes before pruning builds.'
}
$localHead = git -C $repo rev-parse HEAD
$upstreamHead = git -C $repo rev-parse '@{upstream}'
if ($LASTEXITCODE -ne 0 -or $localHead -ne $upstreamHead) {
    throw 'Push the current commit to its upstream branch before pruning builds.'
}

$removed = 0
foreach ($directory in Get-ChildItem -LiteralPath $buildRoot -Directory) {
    $candidate = (Resolve-Path -LiteralPath $directory.FullName).Path
    if ([System.IO.Path]::GetDirectoryName($candidate) -ne $buildRoot -or
        ($directory.Attributes -band [System.IO.FileAttributes]::ReparsePoint)) {
        throw "Unsafe build directory: $candidate"
    }
    if ($candidate -eq $keep) { continue }
    if ($PSCmdlet.ShouldProcess($candidate, 'Remove outdated build')) {
        Remove-Item -LiteralPath $candidate -Recurse -Force
        $removed++
    }
}
Write-Output "Kept $keep; removed $removed older build directories."
