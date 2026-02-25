#!/bin/bash
# Create CoincallTrader Deployment Package for Windows VPS
# Run this on your Mac to create a clean deployment zip file

set -e

echo "=========================================="
echo "CoincallTrader - Deployment Package Creator"
echo "=========================================="
echo ""

# Get the script directory (deployment folder)
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

# Output location
OUTPUT_DIR="$PROJECT_ROOT"
PACKAGE_NAME="CoincallTrader-Deploy-$(date +%Y%m%d-%H%M%S).zip"
PACKAGE_PATH="$OUTPUT_DIR/$PACKAGE_NAME"

echo "📦 Creating deployment package..."
echo "   Source: $PROJECT_ROOT"
echo "   Output: $PACKAGE_PATH"
echo ""

# Create temporary directory for packaging
TEMP_DIR=$(mktemp -d)
DEPLOY_DIR="$TEMP_DIR/CoincallTrader"
mkdir -p "$DEPLOY_DIR"

echo "📋 Copying files..."

# Copy application files
cp "$PROJECT_ROOT"/*.py "$DEPLOY_DIR/" 2>/dev/null || true
cp "$PROJECT_ROOT"/*.txt "$DEPLOY_DIR/" 2>/dev/null || true
cp "$PROJECT_ROOT"/*.md "$DEPLOY_DIR/" 2>/dev/null || true

# Copy .env.example (not .env - user will configure on VPS)
cp "$PROJECT_ROOT/.env.example" "$DEPLOY_DIR/" 2>/dev/null || true

# Copy directories (excluding unwanted files)
rsync -av --progress \
  --exclude='.venv' \
  --exclude='venv' \
  --exclude='__pycache__' \
  --exclude='*.pyc' \
  --exclude='*.pyo' \
  --exclude='*.pyd' \
  --exclude='.Python' \
  --exclude='pip-log.txt' \
  --exclude='pip-delete-this-directory.txt' \
  --exclude='.pytest_cache' \
  --exclude='.coverage' \
  --exclude='htmlcov' \
  --exclude='dist' \
  --exclude='build' \
  --exclude='*.egg-info' \
  --exclude='.git' \
  --exclude='.gitignore' \
  --exclude='.env' \
  --exclude='logs/*.log' \
  --exclude='logs/*.json' \
  --exclude='.DS_Store' \
  "$PROJECT_ROOT/deployment/" "$DEPLOY_DIR/deployment/"

rsync -av \
  --exclude='__pycache__' \
  --exclude='*.pyc' \
  "$PROJECT_ROOT/strategies/" "$DEPLOY_DIR/strategies/" 2>/dev/null || true

rsync -av \
  --exclude='__pycache__' \
  --exclude='*.pyc' \
  "$PROJECT_ROOT/tests/" "$DEPLOY_DIR/tests/" 2>/dev/null || true

rsync -av \
  "$PROJECT_ROOT/docs/" "$DEPLOY_DIR/docs/" 2>/dev/null || true

rsync -av \
  --exclude='__pycache__' \
  --exclude='*.pyc' \
  "$PROJECT_ROOT/archive/" "$DEPLOY_DIR/archive/" 2>/dev/null || true

# Create empty logs directory (will be populated on VPS)
mkdir -p "$DEPLOY_DIR/logs"
touch "$DEPLOY_DIR/logs/.gitkeep"

echo "📝 Creating deployment README..."

# Create a deployment-specific README
cat > "$DEPLOY_DIR/DEPLOY_NOW.md" << 'EOF'
# CoincallTrader - Windows VPS Deployment Instructions

## 📦 You've Extracted the Deployment Package!

This package contains everything needed to deploy CoincallTrader to your Windows Server 2022 VPS.

---

## ✅ Quick Start (5 Steps)

### 1️⃣ Extract to C:\CoincallTrader
   - You should extract this entire folder to `C:\CoincallTrader`
   - The final structure should be: `C:\CoincallTrader\main.py`, `C:\CoincallTrader\deployment\`, etc.

### 2️⃣ Open PowerShell as Administrator
   ```powershell
   # Right-click PowerShell → "Run as Administrator"
   ```

### 3️⃣ Run the Automated Setup
   ```powershell
   cd C:\CoincallTrader\deployment
   .\setup.ps1 -InstallService
   ```
   
   This will:
   - ✅ Check Python installation
   - ✅ Create virtual environment
   - ✅ Install dependencies
   - ✅ Create .env file from template
   - ✅ Install Windows service (using NSSM)

### 4️⃣ Configure Your API Credentials
   ```powershell
   notepad C:\CoincallTrader\.env
   ```
   
   Set these values:
   - `TRADING_ENVIRONMENT=testnet` (or `production`)
   - `COINCALL_API_KEY_TEST=your_key_here`
   - `COINCALL_API_SECRET_TEST=your_secret_here`
   - And/or production credentials

### 5️⃣ Start the Service
   ```powershell
   Start-Service CoincallTrader
   Get-Service CoincallTrader
   ```

---

## 📊 Monitor Your Bot

```powershell
# Live dashboard (auto-refreshing)
cd C:\CoincallTrader\deployment
.\monitor_dashboard.ps1

# View logs in real-time
Get-Content C:\CoincallTrader\logs\trading.log -Tail 20 -Wait
```

---

## 📚 Full Documentation

- **Complete Guide**: See `WINDOWS_DEPLOYMENT.md`
- **Deployment Checklist**: See `deployment/CHECKLIST.md`
- **Quick Commands**: See `deployment/QUICK_REFERENCE.md`
- **VS Code Setup**: See `deployment/VPS_VSCODE_SETUP.md`

---

## ⚠️ Important Notes

1. **Start with TESTNET first** to verify everything works
2. **Python 3.11+ Required**: Install from https://www.python.org/downloads/windows/
3. **Run as Administrator**: The setup script needs admin privileges
4. **Firewall**: Ensure outbound HTTPS (port 443) is allowed for API access
5. **Resource Monitoring**: Watch CPU/RAM usage alongside MultiCharts & TWS

---

## 🆘 Troubleshooting

### Python Not Found?
```powershell
# Install Python 3.11+ from python.org
# Make sure to check "Add Python to PATH" during installation
```

### Service Won't Start?
```powershell
# Check service status
nssm status CoincallTrader

# View error logs
Get-Content C:\CoincallTrader\logs\service_error.log

# Test manually first
cd C:\CoincallTrader
.\.venv\Scripts\Activate.ps1
python main.py
```

### Can't Run Scripts?
```powershell
# Set execution policy
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
```

---

## 🎯 Next Steps After Deployment

1. **Schedule Health Checks**: See `deployment/CHECKLIST.md` for Task Scheduler setup
2. **Set Up Log Rotation**: Automatic cleanup of old logs
3. **Configure Monitoring**: Dashboard and alerting
4. **Backup Strategy**: Important trade data is in `logs/trades_snapshot.json`

---

**Need Help?** Open the project in VS Code on the VPS and use GitHub Copilot for assistance!

EOF

echo "✅ Files copied successfully!"
echo ""

# Create the zip file
echo "🗜️  Creating zip archive..."
cd "$TEMP_DIR"
zip -r "$PACKAGE_PATH" CoincallTrader -q

# Clean up
rm -rf "$TEMP_DIR"

echo ""
echo "=========================================="
echo "✅ Deployment package created successfully!"
echo "=========================================="
echo ""
echo "📦 Package: $PACKAGE_NAME"
echo "📍 Location: $OUTPUT_DIR"
echo ""
echo "📋 Package Contents:"
echo "   ✓ All Python source files"
echo "   ✓ Requirements.txt"
echo "   ✓ Deployment scripts (PowerShell)"
echo "   ✓ Documentation"
echo "   ✓ Strategies folder"
echo "   ✓ .env.example template"
echo ""
echo "❌ Excluded (will be created on VPS):"
echo "   ✗ .venv (virtual environment)"
echo "   ✗ .env (API credentials)"
echo "   ✗ logs/* (log files)"
echo "   ✗ __pycache__, .git, etc."
echo ""
echo "📤 Next Steps:"
echo "   1. Transfer $PACKAGE_NAME to your Windows VPS"
echo "   2. Extract to C:\\CoincallTrader"
echo "   3. Follow instructions in DEPLOY_NOW.md"
echo ""
