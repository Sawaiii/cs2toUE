# The only build script.
#
#   powershell -ExecutionPolicy Bypass -File build.ps1
#
# Does everything from a clean machine:
#   1. finds a working Python, or downloads and installs one into workspace\python
#   2. creates workspace\venv and installs demoparser2 / pandas / PyInstaller
#   3. builds dist\cs2toUE\        - the application (no Python needed to run it)
#   4. builds dist\cs2toUE-Setup.exe - one file that installs the application
#
# Nothing is written to C:. Python, the venv, the pip cache and every temp file live
# under workspace\ next to this script.

param(
    [switch]$AppOnly,           # skip the installer pass
    [string]$PythonVersion = "3.13.9"
)

$ErrorActionPreference = 'Continue'   # native tools report failure through $LASTEXITCODE
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

$ws = Join-Path $root 'workspace'
$env:TMP = Join-Path $ws 'tmp'
$env:TEMP = $env:TMP
$env:PIP_CACHE_DIR = Join-Path $ws 'pipcache'
$env:PYTHONUTF8 = '1'
foreach ($d in @($ws, $env:TMP, $env:PIP_CACHE_DIR, (Join-Path $ws 'downloads'))) {
    New-Item -ItemType Directory -Force -Path $d | Out-Null
}

function Say($m) { Write-Host ":: $m" -ForegroundColor Cyan }
function Ok($m) { Write-Host "OK $m" -ForegroundColor Green }
function Die($m) { Write-Host "XX $m" -ForegroundColor Red; exit 1 }

# ------------------------------------------------------------------ python

function Test-Py($exe) {
    if (-not $exe -or -not (Test-Path $exe)) { return $false }
    try {
        $v = & $exe -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null
        if ($LASTEXITCODE -ne 0 -or -not $v) { return $false }
        $p = $v.Trim().Split('.')
        return ([int]$p[0] -eq 3 -and [int]$p[1] -ge 9)
    } catch { return $false }
}

function Find-Python {
    $candidates = @()
    if ($env:CS2TOUE_PYTHON) { $candidates += $env:CS2TOUE_PYTHON }
    # ask the py launcher to LIST interpreters - it never downloads one that way
    foreach ($line in (& py -0p 2>$null)) {
        $m = [regex]::Match($line, '([A-Za-z]:\\.*python\.exe)')
        if ($m.Success) { $candidates += $m.Groups[1].Value }
    }
    foreach ($g in @("$env:LOCALAPPDATA\Python\pythoncore-*\python.exe",
                     "$env:LOCALAPPDATA\Programs\Python\Python3*\python.exe",
                     "$env:ProgramFiles\Python3*\python.exe")) {
        $candidates += (Get-ChildItem -Path $g -ErrorAction SilentlyContinue |
                        ForEach-Object { $_.FullName })
    }
    $cmd = Get-Command python -ErrorAction SilentlyContinue
    if ($cmd -and $cmd.Source -notlike '*WindowsApps*') { $candidates += $cmd.Source }

    foreach ($c in ($candidates | Where-Object { $_ } | Select-Object -Unique)) {
        if (Test-Py $c) { return $c }
    }
    return $null
}

function Install-Python($version) {
    $exeName = "python-$version-amd64.exe"
    $installer = Join-Path $ws "downloads\$exeName"
    if (-not (Test-Path $installer)) {
        Say "downloading Python $version (about 28 MB)"
        try {
            Invoke-WebRequest -Uri "https://www.python.org/ftp/python/$version/$exeName" `
                              -OutFile $installer -UseBasicParsing
        } catch { Die "could not download Python: $($_.Exception.Message)" }
    }
    $pyHome = Join-Path $ws 'python'
    Say "installing Python $version into $pyHome"
    $args = @('/quiet', 'InstallAllUsers=0', "TargetDir=$pyHome", 'PrependPath=0',
              'Include_pip=1', 'Include_tcltk=1', 'Include_test=0', 'Include_doc=0',
              'Include_launcher=0', 'InstallLauncherAllUsers=0', 'AssociateFiles=0',
              'Shortcuts=0', 'SimpleInstall=1')
    Start-Process -FilePath $installer -ArgumentList $args -Wait | Out-Null
    $exe = Join-Path $pyHome 'python.exe'
    if (Test-Py $exe) { return $exe }
    return $null
}

$local = Join-Path $ws 'python\python.exe'
$python = if (Test-Py $local) { $local } else { Find-Python }
if (-not $python) {
    Say "no usable Python found on this machine"
    $python = Install-Python $PythonVersion
    if (-not $python) { Die "Python installation failed" }
}
Ok "python: $python"

# ------------------------------------------------------------------ venv

$venvPy = Join-Path $ws 'venv\Scripts\python.exe'
if (-not (Test-Path $venvPy)) {
    Say "creating the virtual environment"
    & $python -m venv (Join-Path $ws 'venv')
    if (-not (Test-Path $venvPy)) { Die "could not create the venv" }
}

& $venvPy -c "import demoparser2, pandas, PyInstaller"
if ($LASTEXITCODE -ne 0) {
    Say "installing build dependencies"
    & $venvPy -m pip install --upgrade pip --cache-dir $env:PIP_CACHE_DIR --disable-pip-version-check
    & $venvPy -m pip install -r (Join-Path $root 'requirements.txt') pyinstaller `
        --cache-dir $env:PIP_CACHE_DIR
    if ($LASTEXITCODE -ne 0) { Die "dependency installation failed" }
}
Ok "dependencies ready"

# ------------------------------------------------------------------ data

if (-not (Test-Path (Join-Path $root 'data\hlae_index.json'))) {
    Say "building the HLAE release index"
    & $venvPy -m cs2toue hlae refresh
}

# ------------------------------------------------------------------ build

# The application build is an intermediate artefact and lives under workspace\, so the
# only thing a person sees in the project folder is cs2toUE-Setup.exe.
$appOut = Join-Path $ws 'build\app'
$appDir = Join-Path $appOut 'cs2toUE'

Say "building the application"
& $venvPy -m PyInstaller cs2toUE.spec --noconfirm `
    --workpath (Join-Path $ws 'build') --distpath $appOut
if ($LASTEXITCODE -ne 0) { Die "application build failed" }
$appMB = (Get-ChildItem $appDir -Recurse -File | Measure-Object Length -Sum).Sum / 1MB
Ok ("application ready ({0:N0} MB): {1}" -f $appMB, $appDir)

if ($AppOnly) { exit 0 }

Say "building the installer"
$env:CS2TOUE_APP_DIR = $appDir
& $venvPy -m PyInstaller setup.spec --noconfirm `
    --workpath (Join-Path $ws 'build') --distpath $root
if ($LASTEXITCODE -ne 0) { Die "installer build failed" }

$setup = Join-Path $root 'cs2toUE-Setup.exe'
$setupMB = (Get-Item $setup).Length / 1MB
Write-Host ""
Ok ("cs2toUE-Setup.exe ready ({0:N0} MB) - in the project folder" -f $setupMB)
Write-Host "   give this single file to anyone - it carries the whole program"
Write-Host "   silent install:  cs2toUE-Setup.exe --silent --target D:\Programs\cs2toUE"
