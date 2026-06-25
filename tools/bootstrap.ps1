# BondScribe one-click bootstrap.
#
# Checks for everything BondScribe needs and installs whatever is missing, then
# launches the app. Designed so a non-technical Windows user can just run
# RunBondScribe.bat and have it "just work" - or get a clear message + a log file
# (bondscribe-setup.log) they can send if something goes wrong.
#
# Steps: OS check -> Python 3.10-3.13 -> Node.js 18+ -> Python venv + deps ->
#        Electron -> launch.

$ErrorActionPreference = 'Stop'

# Repo root is the parent of this script's tools/ folder.
$Root    = Split-Path -Parent $PSScriptRoot
$LogFile = Join-Path $Root 'bondscribe-setup.log'
$Desktop = Join-Path $Root 'desktop'

function Log {
    param([string]$Message)
    $line = "[{0}] {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $Message
    try { Add-Content -Path $LogFile -Value $line -Encoding utf8 } catch {}
    Write-Host $line
}

function Fail {
    param([string]$Message)
    Log "ERROR: $Message"
    Write-Host ""
    Write-Host "------------------------------------------------------------" -ForegroundColor Red
    Write-Host "  BondScribe setup could not finish." -ForegroundColor Red
    Write-Host "  $Message" -ForegroundColor Red
    Write-Host ""
    Write-Host "  See TROUBLESHOOTING.md, and send this log file for help:" -ForegroundColor Yellow
    Write-Host "    $LogFile" -ForegroundColor Yellow
    Write-Host "------------------------------------------------------------" -ForegroundColor Red
    Write-Host ""
    Read-Host "Press Enter to close"
    exit 1
}

# Refresh this process's PATH from the registry so a just-installed tool is
# visible without reopening the window (the classic bootstrap gotcha).
function Update-PathFromRegistry {
    $machine = [Environment]::GetEnvironmentVariable('Path', 'Machine')
    $user    = [Environment]::GetEnvironmentVariable('Path', 'User')
    $env:Path = @($machine, $user | Where-Object { $_ }) -join ';'
}

function Has-Winget {
    try { $null = Get-Command winget -ErrorAction Stop; return $true } catch { return $false }
}

# ---------------------------------------------------------------- Python ----
function Test-PythonCandidate {
    param([string]$Exe, [string[]]$PreArgs)
    try {
        $code = "import sys;print('{}.{}'.format(*sys.version_info[:2]))"
        $out = (& $Exe @PreArgs -c $code) 2>$null
        if ($LASTEXITCODE -ne 0 -or -not $out) { return $null }
        $v = ($out | Select-Object -First 1).Trim()
        if ($v -match '^(\d+)\.(\d+)$') {
            $maj = [int]$Matches[1]; $min = [int]$Matches[2]
            if ($maj -eq 3 -and $min -ge 10 -and $min -le 13) {
                return @{ Exe = $Exe; PreArgs = $PreArgs; Version = $v }
            }
        }
    } catch {}
    return $null
}

function Find-Python {
    $candidates = @(
        @{ Exe = 'py';      PreArgs = @('-3.12') },
        @{ Exe = 'py';      PreArgs = @('-3.11') },
        @{ Exe = 'py';      PreArgs = @('-3.13') },
        @{ Exe = 'py';      PreArgs = @('-3.10') },
        @{ Exe = 'py';      PreArgs = @('-3')    },
        @{ Exe = 'python';  PreArgs = @()        },
        @{ Exe = 'python3'; PreArgs = @()        }
    )
    foreach ($c in $candidates) {
        $hit = Test-PythonCandidate -Exe $c.Exe -PreArgs $c.PreArgs
        if ($hit) { return $hit }
    }
    return $null
}

function Install-Python {
    if (Has-Winget) {
        Log "Installing Python 3.12 via winget..."
        winget install --id Python.Python.3.12 -e --silent `
            --accept-package-agreements --accept-source-agreements
        Update-PathFromRegistry
        $hit = Find-Python
        if ($hit) { return $hit }
        Log "winget Python install did not produce a usable python; trying direct download."
    } else {
        Log "winget not available; trying direct Python download."
    }

    try {
        $url = 'https://www.python.org/ftp/python/3.12.7/python-3.12.7-amd64.exe'
        $dst = Join-Path $env:TEMP 'python-3.12.7-amd64.exe'
        Log "Downloading $url"
        Invoke-WebRequest -Uri $url -OutFile $dst -UseBasicParsing
        Log "Running Python installer (silent, per-user, PATH on)..."
        Start-Process -FilePath $dst `
            -ArgumentList '/quiet', 'InstallAllUsers=0', 'PrependPath=1', 'Include_launcher=1' `
            -Wait
        Update-PathFromRegistry
        $hit = Find-Python
        if ($hit) { return $hit }
    } catch {
        Log "Direct Python download failed: $($_.Exception.Message)"
    }
    return $null
}

# ------------------------------------------------------------------ Node ----
function Get-NodeMajor {
    try {
        $v = (& node --version) 2>$null
        if ($LASTEXITCODE -ne 0 -or -not $v) { return 0 }
        if (($v | Select-Object -First 1) -match 'v(\d+)\.') { return [int]$Matches[1] }
    } catch {}
    return 0
}

function Install-Node {
    if (Has-Winget) {
        Log "Installing Node.js LTS via winget..."
        winget install --id OpenJS.NodeJS.LTS -e --silent `
            --accept-package-agreements --accept-source-agreements
        Update-PathFromRegistry
        if ((Get-NodeMajor) -ge 18) { return $true }
        Log "winget Node install did not produce Node 18+; trying direct download."
    } else {
        Log "winget not available; trying direct Node download."
    }

    try {
        $url = 'https://nodejs.org/dist/v20.17.0/node-v20.17.0-x64.msi'
        $dst = Join-Path $env:TEMP 'node-v20.17.0-x64.msi'
        Log "Downloading $url"
        Invoke-WebRequest -Uri $url -OutFile $dst -UseBasicParsing
        Log "Running Node installer (silent)..."
        Start-Process -FilePath 'msiexec.exe' -ArgumentList '/i', "`"$dst`"", '/qn' -Wait
        Update-PathFromRegistry
        if ((Get-NodeMajor) -ge 18) { return $true }
    } catch {
        Log "Direct Node download failed: $($_.Exception.Message)"
    }
    return $false
}

# =================================================================== main ===
try {
    Log "=== BondScribe bootstrap starting ==="
    Log "Root: $Root"

    # ---- Step 1: OS check ----
    if ([Environment]::OSVersion.Platform -ne 'Win32NT') {
        Fail "BondScribe currently supports Windows only."
    }
    if (-not [Environment]::Is64BitOperatingSystem) {
        Fail "BondScribe requires 64-bit Windows."
    }
    Log "OS check OK: $([Environment]::OSVersion.VersionString) (64-bit)"

    # ---- Step 2: Python 3.10-3.13 ----
    Write-Host "Checking for Python 3.10-3.13..."
    $py = Find-Python
    if (-not $py) {
        Write-Host "Python 3.10-3.13 not found. Installing (this may take a few minutes)..."
        $py = Install-Python
    }
    if (-not $py) {
        Fail "Could not find or install Python 3.10-3.13. Install it from https://www.python.org/downloads/ (check 'Add python.exe to PATH'), then re-run."
    }
    Log "Using Python $($py.Version): $($py.Exe) $($py.PreArgs -join ' ')"

    # ---- Step 3: Node.js 18+ ----
    Write-Host "Checking for Node.js 18+..."
    if ((Get-NodeMajor) -lt 18) {
        Write-Host "Node.js 18+ not found. Installing (this may take a few minutes)..."
        if (-not (Install-Node)) {
            Fail "Could not find or install Node.js 18+. Install the LTS from https://nodejs.org, then re-run."
        }
    }
    Log "Using Node.js $((& node --version))"

    # ---- Step 4: Python venv + dependencies ----
    $venvDir = Join-Path $Root '.venv'
    $venvPy  = Join-Path $venvDir 'Scripts\python.exe'
    if (-not (Test-Path $venvPy)) {
        Write-Host "Creating Python environment..."
        Log "Creating venv at $venvDir"
        $pyArgs = $py.PreArgs
        & $py.Exe @pyArgs -m venv $venvDir
        if ($LASTEXITCODE -ne 0 -or -not (Test-Path $venvPy)) {
            Fail "Failed to create the Python virtual environment."
        }
    }

    $flag = Join-Path $venvDir 'installed.flag'
    if (-not (Test-Path $flag)) {
        Write-Host "Installing Python dependencies (first run, takes a few minutes)..."
        Log "pip install -e ."
        & $venvPy -m pip install --upgrade pip
        if ($LASTEXITCODE -ne 0) { Fail "Failed to upgrade pip." }
        & $venvPy -m pip install -e $Root
        if ($LASTEXITCODE -ne 0) { Fail "Failed to install Python dependencies (pip install -e .)." }
        'installed' | Out-File -FilePath $flag -Encoding ascii
        Log "Python dependencies installed."
    } else {
        Log "Python dependencies already installed (installed.flag present)."
    }

    # ---- Step 5: Electron (npm) ----
    if (-not (Test-Path (Join-Path $Desktop 'node_modules\electron'))) {
        Write-Host "Installing Electron (first run, takes a few minutes)..."
        Log "npm install in $Desktop"
        Push-Location $Desktop
        try {
            & npm install
            if ($LASTEXITCODE -ne 0) { Fail "npm install failed." }
        } finally { Pop-Location }
        Log "Electron installed."
    } else {
        Log "Electron already installed."
    }

    # ---- Step 6: Launch ----
    Write-Host ""
    Write-Host "============================================================"
    Write-Host "  Launching BondScribe..."
    Write-Host "  (First run downloads the speech model - the window will"
    Write-Host "   show 'Loading speech model' for a few minutes. Normal.)"
    Write-Host "============================================================"
    Write-Host ""
    Log "Launching Electron (npm start)."
    Push-Location $Desktop
    try {
        & npm start
    } finally { Pop-Location }
    Log "BondScribe exited."
}
catch {
    Fail $_.Exception.Message
}
