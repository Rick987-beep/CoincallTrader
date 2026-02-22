# CoincallTrader Deployment Scripts

This directory contains PowerShell scripts for deploying and managing CoincallTrader on Windows Server 2022.

## 📁 Files

- **setup.ps1** — Automated deployment script
- **health_check.ps1** — Service health monitoring
- **rotate_logs.ps1** — Log rotation and archiving
- **monitor_dashboard.ps1** — Live monitoring dashboard

## 🚀 Quick Start

### 1. Initial Deployment

```powershell
# From your local machine, copy all files to the VPS
# Then on the VPS, run as Administrator:

cd C:\CoincallTrader\deployment
.\setup.ps1 -InstallService
```

### 2. Configure Credentials

```powershell
notepad C:\CoincallTrader\.env
```

### 3. Start the Service

```powershell
Start-Service CoincallTrader
Get-Service CoincallTrader
```

### 4. Monitor

```powershell
# Live dashboard (auto-refreshing)
.\monitor_dashboard.ps1

# One-time status check
.\monitor_dashboard.ps1 -OneShot

# View logs
Get-Content C:\CoincallTrader\logs\trading.log -Tail 20 -Wait
```

## 📋 Scheduled Tasks

Set up these tasks in Windows Task Scheduler:

### Health Check (Every 15 Minutes)

```powershell
# Run as: SYSTEM or Administrator
powershell.exe -ExecutionPolicy Bypass -File "C:\CoincallTrader\deployment\health_check.ps1"
```

### Log Rotation (Daily at 2 AM)

```powershell
# Run as: SYSTEM or Administrator
powershell.exe -ExecutionPolicy Bypass -File "C:\CoincallTrader\deployment\rotate_logs.ps1"
```

## 🛠️ Common Commands

### Service Management

```powershell
# Start service
Start-Service CoincallTrader

# Stop service
Stop-Service CoincallTrader

# Restart service
Restart-Service CoincallTrader

# Check status
Get-Service CoincallTrader

# View service configuration
nssm edit CoincallTrader
```

### Logs

```powershell
# View application log (tail -f equivalent)
Get-Content C:\CoincallTrader\logs\trading.log -Tail 20 -Wait

# View service output
Get-Content C:\CoincallTrader\logs\service_output.log -Tail 20

# View service errors
Get-Content C:\CoincallTrader\logs\service_error.log -Tail 20

# View health check log
Get-Content C:\CoincallTrader\logs\health_check.log -Tail 50
```

### Manual Testing

```powershell
# Stop service first
Stop-Service CoincallTrader

# Activate virtual environment and run manually
cd C:\CoincallTrader
.\.venv\Scripts\Activate.ps1
python main.py

# Press Ctrl+C to stop
```

## 🔧 Script Parameters

### setup.ps1

```powershell
# Full installation with service
.\setup.ps1 -InstallService

# Install to custom path
.\setup.ps1 -InstallPath "D:\Trading\CoincallTrader" -InstallService

# Setup without service (manual testing)
.\setup.ps1
```

### health_check.ps1

```powershell
# With Telegram alerts
.\health_check.ps1 -TelegramBotToken "YOUR_BOT_TOKEN" -TelegramChatId "YOUR_CHAT_ID"

# Custom thresholds
.\health_check.ps1 -MaxLogAgeMinutes 45
```

### rotate_logs.ps1

```powershell
# Custom retention and size limits
.\rotate_logs.ps1 -MaxLogSizeMB 200 -KeepDays 60

# Custom archive location
.\rotate_logs.ps1 -ArchiveDirectory "D:\Backups\CoincallTrader\logs"
```

### monitor_dashboard.ps1

```powershell
# Auto-refreshing dashboard (default 5s)
.\monitor_dashboard.ps1

# Custom refresh rate
.\monitor_dashboard.ps1 -RefreshSeconds 10

# One-time status check
.\monitor_dashboard.ps1 -OneShot
```

## 🚨 Troubleshooting

### Service Won't Start

```powershell
# Check service status
nssm status CoincallTrader

# View error logs
Get-Content C:\CoincallTrader\logs\service_error.log

# Test manually
cd C:\CoincallTrader
.\.venv\Scripts\Activate.ps1
python main.py
```

### Script Execution Policy Errors

```powershell
# Allow script execution for current user
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser

# Or run with bypass
powershell.exe -ExecutionPolicy Bypass -File .\setup.ps1
```

### Permission Errors

```powershell
# Run PowerShell as Administrator
# Right-click PowerShell → "Run as Administrator"
```

## 📊 Monitoring with Telegram (Optional)

To receive alerts via Telegram:

1. Create a Telegram bot: @BotFather
2. Get your chat ID: @userinfobot
3. Update health_check.ps1 parameters:

```powershell
.\health_check.ps1 -TelegramBotToken "1234567890:ABCdefGHIjklMNOpqrsTUVwxyz" -TelegramChatId "123456789"
```

4. Schedule this in Task Scheduler

## 🔄 Updating the Application

```powershell
# Stop service
Stop-Service CoincallTrader

# Update files (git pull or manual copy)
cd C:\CoincallTrader
git pull

# Update dependencies
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt --upgrade

# Restart service
Start-Service CoincallTrader

# Verify
.\deployment\monitor_dashboard.ps1 -OneShot
```

## 📞 Support

For detailed deployment instructions, see: [WINDOWS_DEPLOYMENT.md](../WINDOWS_DEPLOYMENT.md)
