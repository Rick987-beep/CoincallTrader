# Setting Up VS Code + GitHub Copilot on Windows VPS

This guide shows you how to install VS Code and GitHub Copilot on your Windows Server 2022 VPS, so you can work directly on the deployment machine with AI assistance.

---

## 🎯 Why This Approach?

✅ Work directly on the deployment environment  
✅ GitHub Copilot can help with setup commands  
✅ No file transfer issues or path mismatches  
✅ Test and deploy in the same environment  
✅ Continue development locally, deploy from VPS  

---

## 📋 Step 1: RDP into Your VPS

1. Open **Remote Desktop Connection** on your Mac:
   - Press `Cmd + Space`, type "Microsoft Remote Desktop"
   - Or download from: https://apps.apple.com/app/microsoft-remote-desktop/id1295203466

2. Connect to your VPS:
   - Enter VPS IP address
   - Enter username/password
   - Click Connect

---

## 📋 Step 2: Install VS Code on VPS

### Option A: Download from Browser (Recommended)

1. Open Edge/Chrome on the VPS
2. Go to: https://code.visualstudio.com/
3. Download **Windows x64 User Installer** (or System Installer)
4. Run the installer:
   - ✅ Add "Open with Code" to context menu
   - ✅ Add to PATH
   - ✅ Register Code as editor for supported files
5. Finish installation

### Option B: Download via PowerShell

```powershell
# Download VS Code installer
$vscodeUrl = "https://code.visualstudio.com/sha/download?build=stable&os=win32-x64-user"
$installerPath = "$env:TEMP\VSCodeUserSetup.exe"

Invoke-WebRequest -Uri $vscodeUrl -OutFile $installerPath

# Run installer silently with recommended options
Start-Process -FilePath $installerPath -ArgumentList "/VERYSILENT /MERGETASKS=!runcode,addcontextmenufiles,addcontextmenufolders,associatewithfiles,addtopath" -Wait

# Clean up
Remove-Item $installerPath

Write-Host "VS Code installed! Starting VS Code..." -ForegroundColor Green
code
```

---

## 📋 Step 3: Install Python on VPS

```powershell
# Download Python 3.11 installer
$pythonUrl = "https://www.python.org/ftp/python/3.11.8/python-3.11.8-amd64.exe"
$installerPath = "$env:TEMP\python-installer.exe"

Invoke-WebRequest -Uri $pythonUrl -OutFile $installerPath

# Install Python with all features and add to PATH
Start-Process -FilePath $installerPath -ArgumentList "/quiet InstallAllUsers=1 PrependPath=1 Include_test=0" -Wait

# Clean up
Remove-Item $installerPath

# Refresh environment variables
$env:Path = [System.Environment]::GetEnvironmentVariable("Path","Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path","User")

# Verify
python --version
pip --version

Write-Host "Python installed successfully!" -ForegroundColor Green
```

---

## 📋 Step 4: Get Your Code on the VPS

### Option A: Git Clone (Recommended)

```powershell
# Install Git for Windows
winget install --id Git.Git -e --source winget

# Or download from: https://git-scm.com/download/win

# Clone your repository
cd C:\
git clone https://github.com/yourusername/CoincallTrader.git
cd CoincallTrader
```

### Option B: Transfer from Local Machine

**From your Mac:**
```bash
# Using scp (if VPS has SSH enabled)
scp -r /Users/ulrikdeichsel/CoincallTrader user@vps-ip:C:/CoincallTrader
```

**Or use RDP file transfer:**
1. In Remote Desktop, enable local resource sharing
2. Drag and drop files from Mac to VPS

### Option C: Download as ZIP

1. If code is on GitHub, download ZIP
2. Extract to `C:\CoincallTrader`

---

## 📋 Step 5: Open Workspace in VS Code

```powershell
# Open VS Code in the project directory
cd C:\CoincallTrader
code .
```

---

## 📋 Step 6: Install GitHub Copilot Extension

### In VS Code on the VPS:

1. Open Extensions (`Ctrl+Shift+X`)
2. Search for "GitHub Copilot"
3. Click **Install** on:
   - GitHub Copilot
   - GitHub Copilot Chat
4. Sign in with your GitHub account
5. Authorize the extension

**Or via command line:**
```powershell
code --install-extension GitHub.copilot
code --install-extension GitHub.copilot-chat
```

---

## 📋 Step 7: Continue Working with Copilot

Once set up:

1. **Open the workspace** in VS Code on VPS
2. **Open Copilot Chat** (Ctrl+Shift+I or click chat icon)
3. **Ask Copilot to help with deployment:**
   - "Install Python dependencies from requirements.txt"
   - "Create a virtual environment and install packages"
   - "Help me set up this app as a Windows service"
   - "Run the health check script"

### Example Session:

```
You: "I need to deploy this Python trading app as a Windows service. 
     Can you help me install dependencies and configure it?"

Copilot: [Will guide you through the exact commands to run]
```

---

## 📋 Step 8: Install Dependencies

In the VS Code terminal on VPS:

```powershell
# Create virtual environment
python -m venv .venv

# Activate it
.\.venv\Scripts\Activate.ps1

# If you get execution policy error:
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser

# Install dependencies
pip install --upgrade pip
pip install -r requirements.txt
```

---

## 📋 Step 9: Configure Environment

```powershell
# Copy example env file
Copy-Item .env.example .env

# Edit with VS Code
code .env
```

Set your API credentials in the `.env` file.

---

## 📋 Step 10: Deploy with Copilot's Help

Now you can ask Copilot in the VPS VS Code:

- "Run the setup script to install the Windows service"
- "Start the CoincallTrader service"
- "Show me the logs"
- "Run a health check"
- "Help me troubleshoot this error: [paste error]"

---

## 🔄 Workflow

### Local Development (Mac)
1. Write code on your Mac
2. Test in local environment
3. Commit to Git
4. Push to GitHub

### VPS Deployment
1. RDP into VPS
2. Open VS Code on VPS
3. Pull latest code: `git pull`
4. Ask Copilot to help deploy/restart service
5. Monitor with dashboard scripts

---

## ⚡ Quick Commands Reference

### On VPS in VS Code Terminal:

```powershell
# Update code
git pull

# Update dependencies
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt --upgrade

# Run setup
.\deployment\setup.ps1 -InstallService

# Check status
Get-Service CoincallTrader

# View logs
Get-Content logs\trading.log -Tail 20 -Wait

# Monitor
.\deployment\monitor_dashboard.ps1
```

---

## 🎯 Advantages of This Setup

✅ **AI-assisted deployment** — Copilot helps with every command  
✅ **Visual interface** — See files, edit easily  
✅ **Integrated terminal** — Run commands right in VS Code  
✅ **Git integration** — Pull updates easily  
✅ **Same environment** — Develop and deploy in same place  
✅ **Remote debugging** — Can debug directly on production  

---

## 🚨 Security Notes

- Don't commit `.env` with real credentials
- Use strong RDP password
- Consider restricting RDP to your IP only
- Keep VS Code and extensions updated
- Close RDP when not actively deploying

---

## 📞 Next Steps

After setup:
1. Ask Copilot: "Help me deploy CoincallTrader as a Windows service"
2. Follow the prompts
3. Monitor with: `.\deployment\monitor_dashboard.ps1`

---

**You're all set!** Work locally, deploy from VPS with Copilot's help. 🚀
