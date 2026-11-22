param(
    [switch]$Demo,
    [switch]$NoOpen,
    [ValidateRange(1, 65535)][int]$Port
)

$ErrorActionPreference = 'Stop'
if (!$Port) { $Port = if ($Demo) { 8767 } else { 8766 } }
$url = "http://127.0.0.1:$Port"
$python = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
$server = Join-Path $PSScriptRoot 'recruitment-workbench\server.py'

function Get-Workbench {
    try {
        $response = Invoke-RestMethod -Uri "$url/api/workspace" -TimeoutSec 2
        if ($response.PSObject.Properties['configured_parser'] -and
            $response.PSObject.Properties['demo']) {
            return $response
        }
    } catch {
        return $null
    }
}

function Open-Workbench {
    Write-Host "Workbench ready at $url"
    if (!$NoOpen) { Start-Process $url }
}

function Install-App([string]$Id) {
    if (!(Get-Command winget.exe -ErrorAction SilentlyContinue)) {
        throw "Windows Package Manager (winget) is required to install $Id. Install App Installer, then rerun."
    }
    & winget.exe install --id $Id --exact --source winget --accept-source-agreements --accept-package-agreements
    if ($LASTEXITCODE -ne 0) { throw "winget could not install $Id (exit $LASTEXITCODE)." }
}

function Find-Python {
    $ErrorActionPreference = 'SilentlyContinue'
    $candidates = @(
        (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python312\python.exe')
        (Join-Path $env:ProgramFiles 'Python312\python.exe')
        'python.exe'
    )
    if (Get-Command py.exe -ErrorAction SilentlyContinue) {
        $selected = & py.exe -3 -c "import sys; print(sys.executable)" 2>$null
        if ($LASTEXITCODE -eq 0) { $candidates = @($selected) + $candidates }
    }
    foreach ($candidate in $candidates) {
        if ((Get-Command $candidate -ErrorAction SilentlyContinue) -and
            ($candidate -notlike '*\WindowsApps\*')) {
            & $candidate -c "import sys; sys.exit(sys.version_info < (3, 12))" 2>$null
            if ($LASTEXITCODE -eq 0) { return $candidate }
        }
    }
}

try {
    $existing = Get-Workbench
    if ($existing) {
        if ([bool]$existing.demo -ne [bool]$Demo) { throw "Port $Port is in use by a different workbench mode." }
        Open-Workbench
        exit 0
    }

    if (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue) {
        throw "Port $Port is already in use. Select another port with -Port."
    }

    $environmentReady = $false
    if (Test-Path -LiteralPath $python) {
        & $python -c "import sys; sys.exit(sys.version_info < (3, 12))"
        $environmentReady = $LASTEXITCODE -eq 0
    }
    if (!$environmentReady) {
        $basePython = Find-Python
        if (!$basePython) {
            Install-App 'Python.Python.3.12'
            $basePython = Find-Python
            if (!$basePython) { throw 'Python installation finished, but Python 3.12+ could not be located.' }
        }
        & $basePython -m venv (Join-Path $PSScriptRoot '.venv')
        if ($LASTEXITCODE -ne 0) { throw 'Could not create the project Python environment.' }
    }

    Push-Location -LiteralPath $PSScriptRoot
    try {
        & $python -c "import sys; from importlib.util import find_spec; sys.exit(find_spec('websocket') is None)"
        if ($LASTEXITCODE -ne 0) {
            & $python -m ensurepip --upgrade
            if ($LASTEXITCODE -ne 0) { throw 'Could not prepare pip in the project environment.' }
            & $python -m pip install .
            if ($LASTEXITCODE -ne 0) { throw 'Could not install the project dependencies.' }
        }
        & $python $server --help | Out-Null
        if ($LASTEXITCODE -ne 0) { throw 'The workbench cannot import its runtime dependencies.' }
    } finally {
        Pop-Location
    }

    $runtimeName = if ($Demo) { '.demo-runtime' } else { '.runtime' }
    $runtime = Join-Path (Split-Path -Parent $server) $runtimeName
    New-Item -Path $runtime -ItemType Directory -Force | Out-Null
    $log = Join-Path $runtime "launcher-$Port.stderr.log"
    $mode = if ($Demo) { '--demo' } else { '--model-only' }
    $worker = Start-Process -FilePath $python -ArgumentList @("`"$server`"", '--port', $Port, $mode) `
        -WorkingDirectory $PSScriptRoot -WindowStyle Hidden -RedirectStandardError $log -PassThru
    $deadline = [DateTime]::UtcNow.AddSeconds(20)
    while ([DateTime]::UtcNow -lt $deadline -and !$worker.HasExited) {
        $status = Get-Workbench
        if ($status -and [bool]$status.demo -eq [bool]$Demo) {
            Open-Workbench
            exit 0
        }
        Start-Sleep -Milliseconds 300
    }
    throw "Workbench did not start. See $log"
} catch {
    Write-Host $_.Exception.Message -ForegroundColor Red
    exit 1
}
