# CoincallTrader Automated Deployment Script for Windows Server 2022
# Run as Administrator

param(
    [string]$InstallPath = "C:\CoincallTrader",
    [string]$PythonVersion = "3.11",
    [switch]$SkipPythonInstall,
    [switch]$InstallService
)

$ErrorActionPreference = "Stop"

Write-Host "=" * 70 -ForegroundColor Cyan
Write-Host "CoincallTrader — Windows Server 2022 Deployment" -ForegroundColor Cyan
Write-Host "=" * 70 -ForegroundColor Cyan

# Check if running as Administrator
$currentPrincipal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $currentPrincipal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Host "ERROR: This script must be run as Administrator!" -ForegroundColor Red
    exit 1
}

# Step 1: Verify Python Installation
Write-Host "`n[1/8] Checking Python installation..." -ForegroundColor Yellow
try {
    $pythonCmd = Get-Command python -ErrorAction Stop
    $pythonVersionOutput = & python --version 2>&1
    Write-Host "✓ Python found: $pythonVersionOutput" -ForegroundColor Green
    
    # Check version
    if ($pythonVersionOutput -notmatch "Python 3\.(1[0-9]|[9-9])") {
        Write-Host "WARNING: Python 3.9+ required. Found: $pythonVersionOutput" -ForegroundColor Yellow
        if (-not $SkipPythonInstall) {
            Write-Host "Please install Python 3.11+ from https://www.python.org/downloads/" -ForegroundColor Red
            exit 1
        }
    }
} catch {
    Write-Host "✗ Python not found!" -ForegroundColor Red
    Write-Host "Install Python 3.11+ from: https://www.python.org/downloads/windows/" -ForegroundColor Yellow
    Write-Host "Make sure to check 'Add Python to PATH' during installation" -ForegroundColor Yellow
    exit 1
}

# Step 2: Create Installation Directory
Write-Host "`n[2/8] Setting up installation directory..." -ForegroundColor Yellow
if (-not (Test-Path $InstallPath)) {
    New-Item -ItemType Directory -Path $InstallPath -Force | Out-Null
    Write-Host "✓ Created directory: $InstallPath" -ForegroundColor Green
} else {
    Write-Host "✓ Directory exists: $InstallPath" -ForegroundColor Green
}

# Step 3: Create Virtual Environment
Write-Host "`n[3/8] Creating Python virtual environment..." -ForegroundColor Yellow
$venvPath = Join-Path $InstallPath ".venv"
if (-not (Test-Path $venvPath)) {
    & python -m venv $venvPath
    Write-Host "✓ Virtual environment created" -ForegroundColor Green
} else {
    Write-Host "✓ Virtual environment already exists" -ForegroundColor Green
}

# Step 4: Upgrade pip and Install Dependencies
Write-Host "`n[4/8] Installing Python dependencies..." -ForegroundColor Yellow
$pipPath = Join-Path $venvPath "Scripts\pip.exe"
$requirementsPath = Join-Path $InstallPath "requirements.txt"

if (Test-Path $requirementsPath) {
    & $pipPath install --upgrade pip
    & $pipPath install -r $requirementsPath
    Write-Host "✓ Dependencies installed" -ForegroundColor Green
} else {
    Write-Host "WARNING: requirements.txt not found at $requirementsPath" -ForegroundColor Yellow
    Write-Host "Make sure to copy all application files to $InstallPath first!" -ForegroundColor Yellow
}

# Step 5: Setup .env file
Write-Host "`n[5/8] Configuring environment..." -ForegroundColor Yellow
$envPath = Join-Path $InstallPath ".env"
$envExamplePath = Join-Path $InstallPath ".env.example"

if (-not (Test-Path $envPath)) {
    if (Test-Path $envExamplePath) {
        Copy-Item $envExamplePath $envPath
        Write-Host "✓ Created .env from .env.example" -ForegroundColor Green
        Write-Host "⚠ IMPORTANT: Edit $envPath with your API credentials!" -ForegroundColor Yellow
    } else {
        Write-Host "WARNING: .env.example not found. You'll need to create .env manually." -ForegroundColor Yellow
    }
} else {
    Write-Host "✓ .env file already exists" -ForegroundColor Green
}

# Step 6: Create Logs Directory
Write-Host "`n[6/8] Setting up logging..." -ForegroundColor Yellow
$logsPath = Join-Path $InstallPath "logs"
if (-not (Test-Path $logsPath)) {
    New-Item -ItemType Directory -Path $logsPath -Force | Out-Null
    Write-Host "✓ Created logs directory" -ForegroundColor Green
} else {
    Write-Host "✓ Logs directory exists" -ForegroundColor Green
}

# Step 7: Check for NSSM (service installation)
Write-Host "`n[7/8] Checking service manager (NSSM)..." -ForegroundColor Yellow
try {
    $nssmCmd = Get-Command nssm -ErrorAction Stop
    Write-Host "✓ NSSM found: $($nssmCmd.Source)" -ForegroundColor Green
    $hasNSSM = $true
} catch {
    Write-Host "✗ NSSM not found" -ForegroundColor Yellow
    Write-Host "To install as a service, download NSSM from: https://nssm.cc/download" -ForegroundColor Yellow
    Write-Host "Or install via Chocolatey: choco install nssm" -ForegroundColor Yellow
    $hasNSSM = $false
}

# Step 8: Install Windows Service (if requested and NSSM available)
if ($InstallService -and $hasNSSM) {
    Write-Host "`n[8/8] Installing Windows Service..." -ForegroundColor Yellow
    
    $serviceName = "CoincallTrader"
    $pythonExe = Join-Path $venvPath "Scripts\python.exe"
    $mainPy = Join-Path $InstallPath "main.py"
    
    # Check if service already exists
    $existingService = Get-Service -Name $serviceName -ErrorAction SilentlyContinue
    if ($existingService) {
        Write-Host "Service already exists. Removing it first..." -ForegroundColor Yellow
        & nssm stop $serviceName
        & nssm remove $serviceName confirm
        Start-Sleep -Seconds 2
    }
    
    # Install service
    & nssm install $serviceName $pythonExe $mainPy
    & nssm set $serviceName AppDirectory $InstallPath
    & nssm set $serviceName DisplayName "CoincallTrader Bot"
    & nssm set $serviceName Description "Automated options trading bot for Coincall"
    & nssm set $serviceName Start SERVICE_AUTO_START
    
    # Configure logging
    $serviceOutLog = Join-Path $logsPath "service_output.log"
    $serviceErrLog = Join-Path $logsPath "service_error.log"
    & nssm set $serviceName AppStdout $serviceOutLog
    & nssm set $serviceName AppStderr $serviceErrLog
    
    # Configure restart policy
    & nssm set $serviceName AppExit Default Restart
    & nssm set $serviceName AppRestartDelay 5000
    & nssm set $serviceName AppThrottle 10000
    
    Write-Host "✓ Service installed successfully!" -ForegroundColor Green
    Write-Host "`nTo start the service, run:" -ForegroundColor Cyan
    Write-Host "  Start-Service CoincallTrader" -ForegroundColor White
} else {
    Write-Host "`n[8/8] Service installation skipped" -ForegroundColor Yellow
    if ($InstallService -and -not $hasNSSM) {
        Write-Host "Install NSSM first, then run this script with -InstallService" -ForegroundColor Yellow
    }
}

# Final Summary
Write-Host "`n" + ("=" * 70) -ForegroundColor Cyan
Write-Host "Deployment Complete!" -ForegroundColor Green
Write-Host ("=" * 70) -ForegroundColor Cyan

Write-Host "`nNext Steps:" -ForegroundColor Yellow
Write-Host "1. Edit your .env file with API credentials:" -ForegroundColor White
Write-Host "   notepad $envPath" -ForegroundColor Cyan

Write-Host "`n2. Test the application manually:" -ForegroundColor White
Write-Host "   cd $InstallPath" -ForegroundColor Cyan
Write-Host "   .\.venv\Scripts\Activate.ps1" -ForegroundColor Cyan
Write-Host "   python main.py" -ForegroundColor Cyan

if ($hasNSSM -and -not $InstallService) {
    Write-Host "`n3. Install as Windows Service (optional):" -ForegroundColor White
    Write-Host "   .\deployment\setup.ps1 -InstallService" -ForegroundColor Cyan
}

if ($InstallService -and $hasNSSM) {
    Write-Host "`n3. Start the service:" -ForegroundColor White
    Write-Host "   Start-Service CoincallTrader" -ForegroundColor Cyan
    Write-Host "`n4. Check service status:" -ForegroundColor White
    Write-Host "   Get-Service CoincallTrader" -ForegroundColor Cyan
    Write-Host "   Get-Content $logsPath\trading.log -Tail 20" -ForegroundColor Cyan
}

Write-Host "`n" + ("=" * 70) -ForegroundColor Cyan
