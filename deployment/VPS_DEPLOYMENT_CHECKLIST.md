# CoincallTrader VPS Deployment - Complete Checklist

**Target Environment**: Windows Server 2022 (Quantstats VPS)  
**Also Running**: MultiCharts, Trader Workstation (TWS)  
**Date**: _______________

---

## ☑️ PHASE 1: PRE-DEPLOYMENT (On Your Mac)

### Local Verification
- [ ] Application runs successfully in testnet mode locally
- [ ] Latest code tested and working
- [ ] All dependencies listed in requirements.txt
- [ ] Production API credentials obtained from Coincall
  - [ ] Production API Key ready
  - [ ] Production API Secret ready
  - [ ] (Optional) Testnet credentials for VPS testing

### Create Deployment Package
- [ ] Run deployment package script:
  ```bash
  cd /Users/ulrikdeichsel/CoincallTrader/deployment
  chmod +x create_package.sh
  ./create_package.sh
  ```
- [ ] Verify package created: `CoincallTrader-Deploy-YYYYMMDD-HHMMSS.zip`
- [ ] Package size reasonable (~few MB, not including .venv)

### Transfer Preparation
- [ ] Decide transfer method:
  - [ ] RDP drag-and-drop
  - [ ] File transfer tool (WinSCP)
  - [ ] Cloud storage (Dropbox, Google Drive)
  - [ ] Git clone (if using GitHub)
- [ ] Have VPS connection details ready
  - [ ] IP Address: _______________
  - [ ] Username: _______________
  - [ ] Password: _______________

---

## ☑️ PHASE 2: VPS ACCESS & INITIAL SETUP

### Remote Desktop Connection
- [ ] Microsoft Remote Desktop installed on Mac
- [ ] Connect to VPS successfully
- [ ] Verify you have Administrator access
- [ ] Check existing services:
  - [ ] MultiCharts running? Yes / No
  - [ ] Trader Workstation running? Yes / No
  - [ ] CPU usage: ______%
  - [ ] RAM usage: ______%

### Install Python 3.11+
- [ ] Download Python from https://www.python.org/downloads/windows/
- [ ] Run installer with these options:
  - [ ] ✅ Add Python to PATH
  - [ ] ✅ Install for all users
  - [ ] ✅ Include pip
- [ ] Verify installation:
  ```powershell
  python --version     # Should show 3.11.x or higher
  pip --version        # Should show pip version
  ```

### Install VS Code + GitHub Copilot (OPTIONAL but recommended)
- [ ] Download VS Code from https://code.visualstudio.com/
- [ ] Install VS Code
- [ ] Open VS Code
- [ ] Install extensions:
  - [ ] GitHub Copilot
  - [ ] GitHub Copilot Chat
  - [ ] Python (Microsoft)
- [ ] Sign in with GitHub account

### Install Git (OPTIONAL - only if using git clone)
- [ ] Download Git from https://git-scm.com/download/win
- [ ] Install Git
- [ ] Configure:
  ```powershell
  git config --global user.name "Your Name"
  git config --global user.email "your.email@example.com"
  ```

---

## ☑️ PHASE 3: DEPLOY APPLICATION

### Transfer Files
- [ ] Create directory: `C:\CoincallTrader`
- [ ] Transfer deployment package to VPS
- [ ] Extract zip to `C:\CoincallTrader`
- [ ] Verify structure:
  ```
  C:\CoincallTrader\
    ├── main.py
    ├── config.py
    ├── requirements.txt
    ├── .env.example
    ├── deployment\
    ├── strategies\
    ├── docs\
    └── ...
  ```

### Run Automated Setup
- [ ] Open PowerShell as Administrator
- [ ] Allow script execution:
  ```powershell
  Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
  ```
- [ ] Navigate to deployment folder:
  ```powershell
  cd C:\CoincallTrader\deployment
  ```
- [ ] Run setup script:
  ```powershell
  .\setup.ps1 -InstallService
  ```
- [ ] Verify setup completed without errors:
  - [ ] Virtual environment created
  - [ ] Dependencies installed
  - [ ] .env file created
  - [ ] Logs directory created
  - [ ] Windows service installed

---

## ☑️ PHASE 4: CONFIGURATION

### Configure API Credentials
- [ ] Open .env file:
  ```powershell
  notepad C:\CoincallTrader\.env
  ```
- [ ] Set environment (testnet first recommended):
  ```
  TRADING_ENVIRONMENT=testnet
  ```
- [ ] Enter testnet credentials:
  ```
  COINCALL_API_KEY_TEST=your_key_here
  COINCALL_API_SECRET_TEST=your_secret_here
  ```
- [ ] (Later) Enter production credentials:
  ```
  COINCALL_API_KEY_PROD=your_key_here
  COINCALL_API_SECRET_PROD=your_secret_here
  ```
- [ ] Save and close

### Secure .env File
- [ ] Restrict file permissions:
  ```powershell
  icacls "C:\CoincallTrader\.env" /inheritance:r
  icacls "C:\CoincallTrader\.env" /grant:r "Administrators:(F)"
  ```

---

## ☑️ PHASE 5: TESTING

### Manual Test Run (CRITICAL)
- [ ] Open PowerShell (as Administrator)
- [ ] Navigate to project:
  ```powershell
  cd C:\CoincallTrader
  ```
- [ ] Activate virtual environment:
  ```powershell
  .\.venv\Scripts\Activate.ps1
  ```
- [ ] Run application manually:
  ```powershell
  python main.py
  ```
- [ ] Observe output for 2-5 minutes:
  - [ ] No error messages
  - [ ] Successfully connects to Coincall API
  - [ ] Strategies initialize
  - [ ] Health checker running
  - [ ] Trades executing (if conditions met)
- [ ] Press Ctrl+C to stop
- [ ] Review log file:
  ```powershell
  Get-Content C:\CoincallTrader\logs\trading.log -Tail 50
  ```

### Verify Network Connectivity
- [ ] Check firewall allows outbound HTTPS:
  ```powershell
  Test-NetConnection -ComputerName api.coincall.com -Port 443
  ```
- [ ] Should show: `TcpTestSucceeded : True`

---

## ☑️ PHASE 6: SERVICE DEPLOYMENT

### Start Windows Service
- [ ] Start the service:
  ```powershell
  Start-Service CoincallTrader
  ```
- [ ] Check service status:
  ```powershell
  Get-Service CoincallTrader
  # Should show: Status=Running
  ```
- [ ] Verify service output:
  ```powershell
  Get-Content C:\CoincallTrader\logs\service_output.log -Tail 20
  ```
- [ ] Check for errors:
  ```powershell
  Get-Content C:\CoincallTrader\logs\service_error.log -Tail 20
  ```

### Configure Service Auto-Start
- [ ] Set service to start automatically:
  ```powershell
  Set-Service -Name CoincallTrader -StartupType Automatic
  ```
- [ ] Verify:
  ```powershell
  Get-Service CoincallTrader | Select-Object Name, Status, StartType
  ```

### Test Service Restart After Reboot
- [ ] Note current service status
- [ ] Reboot VPS:
  ```powershell
  Restart-Computer
  ```
- [ ] After reboot, reconnect via RDP
- [ ] Check service auto-started:
  ```powershell
  Get-Service CoincallTrader
  # Should show: Status=Running
  ```

---

## ☑️ PHASE 7: MONITORING & MAINTENANCE

### Set Up Scheduled Tasks

#### Health Check (Every 15 Minutes)
- [ ] Open Task Scheduler
- [ ] Create new task:
  - [ ] Name: `CoincallTrader Health Check`
  - [ ] Run as: SYSTEM or Administrator
  - [ ] Trigger: Every 15 minutes
  - [ ] Action: Run program
    ```
    powershell.exe
    -ExecutionPolicy Bypass
    -File "C:\CoincallTrader\deployment\health_check.ps1"
    ```
  - [ ] Settings: Run whether user is logged on or not

#### Log Rotation (Daily at 2 AM)
- [ ] Create new task:
  - [ ] Name: `CoincallTrader Log Rotation`
  - [ ] Run as: SYSTEM or Administrator
  - [ ] Trigger: Daily at 2:00 AM
  - [ ] Action: Run program
    ```
    powershell.exe
    -ExecutionPolicy Bypass
    -File "C:\CoincallTrader\deployment\rotate_logs.ps1"
    ```

### Set Up Monitoring Dashboard
- [ ] Test dashboard:
  ```powershell
  cd C:\CoincallTrader\deployment
  .\monitor_dashboard.ps1 -OneShot
  ```
- [ ] Verify displays:
  - [ ] Service status
  - [ ] Recent trades
  - [ ] Open positions
  - [ ] Account balance
  - [ ] System resources

---

## ☑️ PHASE 8: PRODUCTION READINESS

### Switch to Production (When Ready)
- [ ] Stop service:
  ```powershell
  Stop-Service CoincallTrader
  ```
- [ ] Edit .env:
  ```powershell
  notepad C:\CoincallTrader\.env
  ```
- [ ] Change to production:
  ```
  TRADING_ENVIRONMENT=production
  ```
- [ ] Save and close
- [ ] Test manually first (at least 10 minutes):
  ```powershell
  cd C:\CoincallTrader
  .\.venv\Scripts\Activate.ps1
  python main.py
  ```
- [ ] Verify production connection working
- [ ] Press Ctrl+C to stop
- [ ] Start service:
  ```powershell
  Start-Service CoincallTrader
  ```

### Final Production Checks
- [ ] Monitor for first hour continuously
- [ ] Check logs every 15 minutes initially
- [ ] Verify trades executing as expected
- [ ] Check account balance/positions match expectations
- [ ] Monitor resource usage (CPU/RAM) alongside MultiCharts/TWS
- [ ] Document any issues in logs

---

## ☑️ PHASE 9: ONGOING OPERATIONS

### Daily Checks
- [ ] Morning: Check service status
- [ ] Morning: Review overnight logs
- [ ] Evening: Verify trades executed
- [ ] Evening: Check account balance

### Weekly Checks
- [ ] Review all logs for errors
- [ ] Check disk space usage
- [ ] Verify scheduled tasks running
- [ ] Review trading performance
- [ ] Update application if needed

### Monthly Checks
- [ ] Windows Updates
- [ ] Python/dependency updates (test first!)
- [ ] Review and archive old logs
- [ ] Performance optimization review

---

## 📝 NOTES & OBSERVATIONS

**Deployment Date**: _______________  
**Deployed By**: _______________

### Issues Encountered:
```
(Record any problems and solutions here)




```

### Performance Metrics:
```
CPU Usage (avg): ______%
RAM Usage (avg): ______% 
Disk Usage: ______GB
Trading Frequency: ______ trades/day
```

### Important Configuration Decisions:
```
(Document any custom settings or deviations from standard deployment)




```

---

## 🆘 EMERGENCY PROCEDURES

### Stop Trading Immediately
```powershell
Stop-Service CoincallTrader
```

### View Recent Activity
```powershell
Get-Content C:\CoincallTrader\logs\trading.log -Tail 100
```

### Rollback to Manual Control
```powershell
Stop-Service CoincallTrader
# Manage positions manually through Coincall web interface
```

### Support Resources
- Documentation: `C:\CoincallTrader\docs\`
- Deployment Guides: `C:\CoincallTrader\deployment\`
- Coincall API Docs: https://docs.coincall.com/
- GitHub Copilot: Available in VS Code for troubleshooting

---

## ✅ DEPLOYMENT COMPLETE

Once all checkboxes above are complete, your CoincallTrader is fully deployed and operational!

**Final Sign-Off**:
- [ ] All phases completed
- [ ] Service running continuously for 24+ hours
- [ ] No critical errors in logs
- [ ] Trading performance meets expectations
- [ ] Monitoring and alerts working
- [ ] Documentation updated with any custom changes

**Status**: ☐ In Progress  ☐ Testing  ☐ Production  

**Deployment Completed**: _______________ (Date & Time)
