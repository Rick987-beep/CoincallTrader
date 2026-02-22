# CoincallTrader Health Check Script
# Monitors the service and restarts if needed
# Schedule this to run every 15 minutes via Task Scheduler

param(
    [string]$ServiceName = "CoincallTrader",
    [string]$LogPath = "C:\CoincallTrader\logs\trading.log",
    [int]$MaxLogAgeMinutes = 30,
    [string]$AlertEmail = "",  # Optional: email for alerts
    [string]$TelegramBotToken = "",  # Optional: Telegram bot token
    [string]$TelegramChatId = ""  # Optional: Telegram chat ID
)

$ErrorActionPreference = "SilentlyContinue"
$timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
$healthLogPath = "C:\CoincallTrader\logs\health_check.log"

function Write-HealthLog {
    param([string]$Message, [string]$Level = "INFO")
    $logMessage = "$timestamp [$Level] $Message"
    Add-Content -Path $healthLogPath -Value $logMessage
    Write-Host $logMessage
}

function Send-Alert {
    param([string]$Message)
    
    Write-HealthLog "ALERT: $Message" "CRITICAL"
    
    # Optional: Send Telegram alert
    if ($TelegramBotToken -and $TelegramChatId) {
        try {
            $telegramUrl = "https://api.telegram.org/bot$TelegramBotToken/sendMessage"
            $body = @{
                chat_id = $TelegramChatId
                text = "🚨 CoincallTrader Alert`n`n$Message`n`n$timestamp"
            }
            Invoke-RestMethod -Uri $telegramUrl -Method Post -Body $body | Out-Null
            Write-HealthLog "Telegram alert sent" "INFO"
        } catch {
            Write-HealthLog "Failed to send Telegram alert: $_" "ERROR"
        }
    }
}

Write-HealthLog "Starting health check..." "INFO"

# Check 1: Is the service running?
$service = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
if (-not $service) {
    Write-HealthLog "Service not found: $ServiceName" "CRITICAL"
    Send-Alert "Service '$ServiceName' is not installed!"
    exit 1
}

if ($service.Status -ne "Running") {
    Write-HealthLog "Service is not running. Status: $($service.Status)" "CRITICAL"
    Send-Alert "Service '$ServiceName' is not running! Attempting restart..."
    
    try {
        Start-Service -Name $ServiceName
        Start-Sleep -Seconds 5
        $service = Get-Service -Name $ServiceName
        
        if ($service.Status -eq "Running") {
            Write-HealthLog "Service restarted successfully" "INFO"
            Send-Alert "✅ Service '$ServiceName' has been restarted successfully"
        } else {
            Write-HealthLog "Failed to restart service" "CRITICAL"
            Send-Alert "❌ Failed to restart service '$ServiceName'!"
            exit 1
        }
    } catch {
        Write-HealthLog "Error restarting service: $_" "CRITICAL"
        Send-Alert "❌ Error restarting service '$ServiceName': $_"
        exit 1
    }
} else {
    Write-HealthLog "Service is running" "INFO"
}

# Check 2: Is the log file being updated?
if (Test-Path $LogPath) {
    $logFile = Get-Item $LogPath
    $logAge = (Get-Date) - $logFile.LastWriteTime
    
    if ($logAge.TotalMinutes -gt $MaxLogAgeMinutes) {
        Write-HealthLog "Log file hasn't been updated in $($logAge.TotalMinutes) minutes (threshold: $MaxLogAgeMinutes)" "WARNING"
        Send-Alert "⚠️ Log file stale! Last update: $($logAge.TotalMinutes) minutes ago. Service may be stuck."
        
        # Restart service if log is stale
        Write-HealthLog "Restarting service due to stale logs..." "WARNING"
        try {
            Restart-Service -Name $ServiceName -Force
            Start-Sleep -Seconds 5
            Write-HealthLog "Service restarted due to stale logs" "INFO"
            Send-Alert "🔄 Service restarted due to stale logs"
        } catch {
            Write-HealthLog "Failed to restart service: $_" "CRITICAL"
            Send-Alert "❌ Failed to restart service: $_"
        }
    } else {
        Write-HealthLog "Log file is current (last updated $([math]::Round($logAge.TotalMinutes, 1)) minutes ago)" "INFO"
    }
} else {
    Write-HealthLog "Log file not found: $LogPath" "WARNING"
}

# Check 3: Check for errors in recent logs
if (Test-Path $LogPath) {
    $recentLogs = Get-Content $LogPath -Tail 50 | Out-String
    
    # Check for common error patterns
    $errorPatterns = @("ERROR", "CRITICAL", "Exception", "Traceback", "Failed to")
    $foundErrors = @()
    
    foreach ($pattern in $errorPatterns) {
        if ($recentLogs -match $pattern) {
            $foundErrors += $pattern
        }
    }
    
    if ($foundErrors.Count -gt 0) {
        Write-HealthLog "Found error patterns in recent logs: $($foundErrors -join ', ')" "WARNING"
        # Note: Not sending alert for every error, as some may be expected
    }
}

# Check 4: CPU and Memory usage
$pythonProcesses = Get-Process -Name python -ErrorAction SilentlyContinue
if ($pythonProcesses) {
    foreach ($proc in $pythonProcesses) {
        $cpu = [math]::Round($proc.CPU, 2)
        $memMB = [math]::Round($proc.WorkingSet64 / 1MB, 2)
        Write-HealthLog "Python process (PID $($proc.Id)): CPU=$cpu, Memory=${memMB}MB" "INFO"
        
        # Alert if memory usage is excessive (> 1GB)
        if ($memMB -gt 1024) {
            Write-HealthLog "High memory usage detected: ${memMB}MB" "WARNING"
            Send-Alert "⚠️ High memory usage: ${memMB}MB"
        }
    }
} else {
    Write-HealthLog "No Python processes found (service may not be running)" "WARNING"
}

# Check 5: Disk space
$drive = Get-PSDrive -Name C
$freeSpaceGB = [math]::Round($drive.Free / 1GB, 2)
if ($freeSpaceGB -lt 5) {
    Write-HealthLog "Low disk space: ${freeSpaceGB}GB free" "CRITICAL"
    Send-Alert "🔴 Low disk space: ${freeSpaceGB}GB free on C:"
} else {
    Write-HealthLog "Disk space OK: ${freeSpaceGB}GB free" "INFO"
}

Write-HealthLog "Health check complete" "INFO"
Write-HealthLog "----------------------------------------" "INFO"

exit 0
