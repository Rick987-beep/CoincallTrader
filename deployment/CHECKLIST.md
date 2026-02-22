# CoincallTrader Windows Server 2022 — Deployment Checklist

Use this checklist to ensure a complete, safe deployment for 7+ day continuous operation.

---

## □ Pre-Deployment (On Local Machine)

- [ ] Test application locally in testnet mode
- [ ] Verify all dependencies in requirements.txt
- [ ] Document any custom configuration needed
- [ ] Obtain production API credentials from Coincall
- [ ] Choose deployment strategy: service, manual, or scheduled task
- [ ] Prepare backup/rollback plan

---

## □ VPS Provisioning

- [ ] Windows Server 2022 instance created
- [ ] Minimum specs met: 2 vCPU, 4GB RAM, 50GB SSD
- [ ] Administrator access obtained
- [ ] Remote Desktop (RDP) access verified
- [ ] Network connectivity confirmed

---

## □ Initial Server Configuration

- [ ] Install all Windows updates
  ```powershell
  Install-Module PSWindowsUpdate -Force
  Get-WindowsUpdate
  Install-WindowsUpdate -AcceptAll
  ```
- [ ] Configure Windows Firewall (RDP only)
- [ ] Disable unnecessary services
- [ ] Set appropriate timezone
- [ ] Configure automatic security updates
- [ ] Create service user account (optional but recommended)

---

## □ Python Installation

- [ ] Download Python 3.11+ from python.org
- [ ] Install with "Add to PATH" option
- [ ] Install for all users
- [ ] Verify: `python --version`
- [ ] Verify: `pip --version`

---

## □ Application Deployment

- [ ] Create directory: `C:\CoincallTrader`
- [ ] Transfer all application files to VPS
  - Option A: Git clone
  - Option B: RDP file transfer
  - Option C: SFTP/SCP
- [ ] Verify all files present:
  - [ ] main.py
  - [ ] config.py
  - [ ] strategy.py
  - [ ] requirements.txt
  - [ ] .env.example
  - [ ] deployment/ folder
  - [ ] All strategy modules

---

## □ Environment Setup

- [ ] Create virtual environment: `python -m venv .venv`
- [ ] Activate venv: `.\.venv\Scripts\Activate.ps1`
- [ ] Upgrade pip: `pip install --upgrade pip`
- [ ] Install dependencies: `pip install -r requirements.txt`
- [ ] Verify no installation errors

---

## □ Configuration

- [ ] Copy `.env.example` to `.env`
- [ ] Edit `.env` with production credentials:
  - [ ] Set `TRADING_ENVIRONMENT=production` (or testnet)
  - [ ] Add `COINCALL_API_KEY_PROD`
  - [ ] Add `COINCALL_API_SECRET_PROD`
- [ ] Restrict .env file permissions
  ```powershell
  icacls "C:\CoincallTrader\.env" /inheritance:r
  icacls "C:\CoincallTrader\.env" /grant:r "Administrators:(F)"
  ```
- [ ] Create logs directory: `mkdir logs`

---

## □ Manual Testing

- [ ] Test run application manually:
  ```powershell
  cd C:\CoincallTrader
  .\.venv\Scripts\Activate.ps1
  python main.py
  ```
- [ ] Verify correct environment (testnet/production) is loaded
- [ ] Verify API connection successful
- [ ] Verify logging works (`logs/trading.log` created)
- [ ] Test for at least 5 minutes
- [ ] Stop gracefully (Ctrl+C)
- [ ] No critical errors in logs

---

## □ Service Installation (NSSM)

- [ ] Download NSSM from https://nssm.cc/download
- [ ] Install NSSM (or use `choco install nssm`)
- [ ] Run automated setup:
  ```powershell
  cd C:\CoincallTrader\deployment
  .\setup.ps1 -InstallService
  ```
  Or manual installation:
  ```powershell
  nssm install CoincallTrader "C:\CoincallTrader\.venv\Scripts\python.exe" "C:\CoincallTrader\main.py"
  nssm set CoincallTrader AppDirectory "C:\CoincallTrader"
  nssm set CoincallTrader Start SERVICE_AUTO_START
  ```
- [ ] Configure service logging paths
- [ ] Set restart policy (auto-restart on failure)
- [ ] Verify service installed: `Get-Service CoincallTrader`

---

## □ Service Testing

- [ ] Start service: `Start-Service CoincallTrader`
- [ ] Check status: `Get-Service CoincallTrader` (should be "Running")
- [ ] View logs: `Get-Content C:\CoincallTrader\logs\trading.log -Tail 20 -Wait`
- [ ] Verify trades/positions are being monitored
- [ ] Test service restart: `Restart-Service CoincallTrader`
- [ ] Verify auto-restart after crash (kill python.exe process)
- [ ] Check Event Viewer for service errors

---

## □ Monitoring Setup

- [ ] Set up health check scheduled task:
  - Task name: "CoincallTrader Health Check"
  - Trigger: Every 15 minutes
  - Action: `powershell.exe -ExecutionPolicy Bypass -File "C:\CoincallTrader\deployment\health_check.ps1"`
  - Run with highest privileges
  - Run whether user is logged on or not

- [ ] Set up log rotation scheduled task:
  - Task name: "CoincallTrader Log Rotation"
  - Trigger: Daily at 2:00 AM
  - Action: `powershell.exe -ExecutionPolicy Bypass -File "C:\CoincallTrader\deployment\rotate_logs.ps1"`
  - Run with highest privileges

- [ ] (Optional) Configure Telegram alerts:
  - [ ] Create Telegram bot
  - [ ] Get chat ID
  - [ ] Update health_check.ps1 parameters
  - [ ] Test alert: `.\deployment\health_check.ps1 -TelegramBotToken "TOKEN" -TelegramChatId "ID"`

---

## □ Security Hardening

- [ ] Configure Windows Firewall:
  - [ ] Block all inbound except RDP
  - [ ] (Optional) Restrict RDP to specific IPs
- [ ] Disable unnecessary Windows features
- [ ] Enable Windows Defender
- [ ] Configure automatic security updates
- [ ] Set strong Administrator password
- [ ] Review service account permissions
- [ ] Restrict .env file access
- [ ] Enable logging for security events

---

## □ Backup & Recovery

- [ ] Document current configuration
- [ ] Backup .env file (store securely, NOT in git!)
- [ ] Test service stop/start procedure
- [ ] Test application update procedure
- [ ] Document rollback steps
- [ ] Schedule periodic configuration backups

---

## □ 7-Day Continuous Operation Test

- [ ] Day 1: Deploy and start service
- [ ] Day 1: Monitor first 4 hours continuously
- [ ] Day 2: Check logs, verify no crashes
- [ ] Day 3: Check logs, verify positions
- [ ] Day 4: Mid-week check
- [ ] Day 5: Check logs, verify health
- [ ] Day 6: Pre-weekend check
- [ ] Day 7: Full week verification
- [ ] Review all logs for the week
- [ ] Check for memory leaks (increasing memory usage)
- [ ] Verify disk space adequate
- [ ] Test service still responding
- [ ] Review any errors/warnings

---

## □ Post-Deployment Documentation

- [ ] Document actual server specs used
- [ ] Document any custom configuration
- [ ] Note any caveats or issues encountered
- [ ] Update runbook with server-specific details
- [ ] Share deployment notes with team
- [ ] Schedule first update/maintenance window

---

## □ Operational Procedures

- [ ] Document how to check status
- [ ] Document how to view logs
- [ ] Document how to restart service
- [ ] Document how to update application
- [ ] Document emergency stop procedure
- [ ] Document who to contact for issues
- [ ] Set up alerting for production issues

---

## ✅ Final Verification

Before considering deployment complete:

```powershell
# 1. Service is running
Get-Service CoincallTrader

# 2. Logs are being written
Get-Content C:\CoincallTrader\logs\trading.log -Tail 5

# 3. Health check passes
.\deployment\health_check.ps1

# 4. Dashboard shows green
.\deployment\monitor_dashboard.ps1 -OneShot

# 5. Scheduled tasks exist
Get-ScheduledTask | Where-Object {$_.TaskName -like "*Coincall*"}

# 6. No errors in Event Viewer
Get-EventLog -LogName Application -Source "NSSM" -Newest 10
```

All checks pass? ✅ **Deployment Complete!**

---

## 🚨 Emergency Contacts

| Issue | Contact | How to Reach |
|-------|---------|--------------|
| Service down | [Your Name] | [Phone/Telegram] |
| API issues | Coincall Support | support@coincall.com |
| VPS issues | [Provider] | [Support link] |

---

**Deployment Date:** _______________  
**Deployed By:** _______________  
**Server IP:** _______________  
**Notes:** 
```


```
