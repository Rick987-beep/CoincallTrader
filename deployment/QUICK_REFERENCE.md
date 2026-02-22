# CoincallTrader — Quick Reference Card

## 🎯 Initial Setup (One-Time)

```powershell
# 1. Copy files to VPS (C:\CoincallTrader)

# 2. Run setup as Administrator
cd C:\CoincallTrader\deployment
.\setup.ps1 -InstallService

# 3. Configure credentials
notepad C:\CoincallTrader\.env

# 4. Start service
Start-Service CoincallTrader
```

---

## ⚡ Daily Commands

```powershell
# Check if running
Get-Service CoincallTrader

# View live logs
Get-Content C:\CoincallTrader\logs\trading.log -Tail 20 -Wait

# Live dashboard
cd C:\CoincallTrader\deployment
.\monitor_dashboard.ps1

# Quick status
.\monitor_dashboard.ps1 -OneShot
```

---

## 🔄 Service Control

```powershell
Start-Service CoincallTrader      # Start
Stop-Service CoincallTrader       # Stop
Restart-Service CoincallTrader    # Restart
Get-Service CoincallTrader        # Status
```

---

## 📊 Logs

```powershell
# Application log (main)
Get-Content C:\CoincallTrader\logs\trading.log -Tail 50

# Service output
Get-Content C:\CoincallTrader\logs\service_output.log -Tail 50

# Service errors
Get-Content C:\CoincallTrader\logs\service_error.log -Tail 50

# Health checks
Get-Content C:\CoincallTrader\logs\health_check.log -Tail 50
```

---

## 🛠️ Troubleshooting

```powershell
# Service won't start? Check status:
nssm status CoincallTrader

# View errors:
Get-Content C:\CoincallTrader\logs\service_error.log

# Test manually:
Stop-Service CoincallTrader
cd C:\CoincallTrader
.\.venv\Scripts\Activate.ps1
python main.py
# Ctrl+C to stop

# Reinstall service:
nssm remove CoincallTrader confirm
cd C:\CoincallTrader\deployment
.\setup.ps1 -InstallService
```

---

## 🔧 Maintenance

```powershell
# Rotate logs manually
cd C:\CoincallTrader\deployment
.\rotate_logs.ps1

# Run health check manually
.\health_check.ps1

# Update application
Stop-Service CoincallTrader
git pull  # or copy new files
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt --upgrade
Start-Service CoincallTrader
```

---

## 🚨 Emergency Procedures

### Service Crashed

```powershell
# Check status
Get-Service CoincallTrader
.\deployment\monitor_dashboard.ps1 -OneShot

# View errors
Get-Content logs\service_error.log -Tail 50

# Restart
Restart-Service CoincallTrader
```

### High Memory Usage

```powershell
# Check processes
Get-Process python | Select-Object Id,CPU,@{Name="MemoryMB";Expression={[math]::Round($_.WS/1MB,2)}}

# Restart service
Restart-Service CoincallTrader
```

### Disk Full

```powershell
# Check space
Get-PSDrive C

# Archive old logs
.\deployment\rotate_logs.ps1

# Or manually clean
Remove-Item C:\CoincallTrader\logs\archive\*.log -Force
```

---

## 📋 Scheduled Tasks to Set Up

1. **Health Check** — Every 15 minutes
   ```
   powershell.exe -ExecutionPolicy Bypass -File "C:\CoincallTrader\deployment\health_check.ps1"
   ```

2. **Log Rotation** — Daily at 2 AM
   ```
   powershell.exe -ExecutionPolicy Bypass -File "C:\CoincallTrader\deployment\rotate_logs.ps1"
   ```

---

## 📱 Key Files

- **Config**: `C:\CoincallTrader\.env`
- **Main Log**: `C:\CoincallTrader\logs\trading.log`
- **Scripts**: `C:\CoincallTrader\deployment\`
- **Service**: `nssm edit CoincallTrader`

---

## ✅ Health Check Criteria

- ✅ Service Status = Running
- ✅ Log updated < 30 minutes ago
- ✅ Memory usage < 1GB
- ✅ Disk space > 5GB free
- ✅ No critical errors in recent logs

---

**Print this and keep it handy!**
