@echo off
REM CoincallTrader Setup Wizard for Windows Server 2022
REM Double-click this file to start automated setup
REM Must be run as Administrator

echo ========================================================================
echo CoincallTrader — Windows Server 2022 Setup Wizard
echo ========================================================================
echo.

REM Check for admin privileges
net session >nul 2>&1
if %errorLevel% neq 0 (
    echo ERROR: This script must be run as Administrator!
    echo.
    echo Right-click this file and select "Run as administrator"
    echo.
    pause
    exit /b 1
)

echo Running as Administrator - OK
echo.

REM Check if PowerShell is available
powershell -Command "Write-Host 'PowerShell available'" >nul 2>&1
if %errorLevel% neq 0 (
    echo ERROR: PowerShell is not available!
    pause
    exit /b 1
)

echo.
echo This wizard will:
echo   1. Check Python installation
echo   2. Create virtual environment
echo   3. Install dependencies
echo   4. Configure environment
echo   5. Install Windows Service (optional)
echo.
echo Press any key to continue, or Ctrl+C to cancel...
pause >nul

echo.
echo Starting PowerShell setup script...
echo.

REM Run the PowerShell setup script
powershell -ExecutionPolicy Bypass -File "%~dp0setup.ps1" -InstallService

if %errorLevel% equ 0 (
    echo.
    echo ========================================================================
    echo Setup completed successfully!
    echo ========================================================================
    echo.
    echo NEXT STEPS:
    echo   1. Edit C:\CoincallTrader\.env with your API credentials
    echo   2. Start the service: Start-Service CoincallTrader
    echo   3. Monitor: cd deployment ^&^& .\monitor_dashboard.ps1
    echo.
) else (
    echo.
    echo ========================================================================
    echo Setup encountered errors!
    echo ========================================================================
    echo.
    echo Check the error messages above and fix any issues.
    echo.
)

pause
