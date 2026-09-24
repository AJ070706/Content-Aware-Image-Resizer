$ErrorActionPreference = 'Stop'
# Build an isolated source snapshot, native module, UI bundle, and one-file app.
# Keep every build until a verified commit is pushed; prune-builds.ps1 handles retention.
$repo = Split-Path $PSScriptRoot -Parent
$python = Join-Path $repo '.venv\Scripts\python.exe'
if (!(Test-Path $python)) { throw 'Create .venv and install requirements-build.txt first. See README.md.' }
$build = Join-Path $repo ('build\' + (Get-Date -Format 'yyyyMMdd-HHmmss-fff'))
New-Item -ItemType Directory -Path $build | Out-Null
Start-Transcript -Path (Join-Path $build 'build.log') | Out-Null
function Run-Checked([string]$Program, [string[]]$Arguments) {
    & $Program @Arguments
    if ($LASTEXITCODE -ne 0) { throw "$Program failed with exit code $LASTEXITCODE" }
}
try {
    # A unique directory makes failed builds inspectable without overwriting prior output.
    $source = Join-Path $build 'source'
    New-Item -ItemType Directory -Path $source,(Join-Path $build 'app') | Out-Null
    Copy-Item (Join-Path $repo 'src\*.py'),(Join-Path $repo 'src\*.cpp') $source
    Copy-Item (Join-Path $repo 'frontend') (Join-Path $source 'frontend') -Recurse -Exclude node_modules,dist
    Copy-Item (Join-Path $repo 'requirements-build.txt'),(Join-Path $repo 'README.md'),$PSCommandPath $source
    Push-Location (Join-Path $source 'frontend')
    try {
        Run-Checked 'npm.cmd' @('ci','--no-audit','--no-fund')
        Run-Checked 'npm.cmd' @('run','build')
    } finally { Pop-Location }
    Copy-Item (Join-Path $source 'frontend\dist') (Join-Path $build 'app\ui') -Recurse
    Push-Location $source
    try { Run-Checked $python @('setup.py','build_ext','--build-lib','../app','--build-temp','../temp') } finally { Pop-Location }
    Copy-Item (Join-Path $source 'desktop.py') (Join-Path $build 'app\desktop.py')
    # PyInstaller embeds the frontend and native extension in a single Windows executable.
    Run-Checked $python @('-m','PyInstaller','--noconfirm','--onefile','--windowed','--name','ImageResizer','--distpath',(Join-Path $build 'release'),'--workpath',(Join-Path $build 'package'),'--specpath',$build,'--paths',(Join-Path $build 'app'),'--hidden-import','main','--add-data',((Join-Path $build 'app\ui') + ';ui'),(Join-Path $build 'app\desktop.py'))
    & $python -m pip freeze | Set-Content (Join-Path $build 'dependencies.txt')
    git -C $repo rev-parse HEAD | Set-Content (Join-Path $build 'source-base-commit.txt')
    Write-Output "BUILD_DIRECTORY=$build"
} finally { Stop-Transcript | Out-Null }
