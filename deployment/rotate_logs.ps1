# CoincallTrader Log Rotation Script
# Keeps logs manageable by archiving old logs
# Schedule this to run daily at 2 AM via Task Scheduler

param(
    [string]$LogDirectory = "C:\CoincallTrader\logs",
    [int]$MaxLogSizeMB = 100,
    [int]$KeepDays = 30,
    [string]$ArchiveDirectory = "C:\CoincallTrader\logs\archive"
)

$ErrorActionPreference = "Stop"
$timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
$dateStamp = Get-Date -Format "yyyyMMdd_HHmmss"

Write-Host "[$timestamp] Starting log rotation..." -ForegroundColor Cyan

# Create archive directory if it doesn't exist
if (-not (Test-Path $ArchiveDirectory)) {
    New-Item -ItemType Directory -Path $ArchiveDirectory -Force | Out-Null
    Write-Host "Created archive directory: $ArchiveDirectory" -ForegroundColor Green
}

# Get all log files
$logFiles = Get-ChildItem -Path $LogDirectory -Filter "*.log" -File

foreach ($logFile in $logFiles) {
    $fileSizeMB = [math]::Round($logFile.Length / 1MB, 2)
    
    Write-Host "`nChecking: $($logFile.Name) (${fileSizeMB}MB)" -ForegroundColor Yellow
    
    # Archive if file is too large
    if ($fileSizeMB -gt $MaxLogSizeMB) {
        $archiveName = "$($logFile.BaseName)_$dateStamp.log"
        $archivePath = Join-Path $ArchiveDirectory $archiveName
        
        try {
            # Stop the service briefly to release the log file
            $serviceName = "CoincallTrader"
            $service = Get-Service -Name $serviceName -ErrorAction SilentlyContinue
            $wasRunning = $false
            
            if ($service -and $service.Status -eq "Running") {
                Write-Host "  Stopping service temporarily..." -ForegroundColor Yellow
                Stop-Service -Name $serviceName -Force
                $wasRunning = $true
                Start-Sleep -Seconds 2
            }
            
            # Move the log file to archive
            Move-Item -Path $logFile.FullName -Destination $archivePath -Force
            Write-Host "  ✓ Archived to: $archiveName" -ForegroundColor Green
            
            # Create a new empty log file
            New-Item -Path $logFile.FullName -ItemType File -Force | Out-Null
            Write-Host "  ✓ Created new log file" -ForegroundColor Green
            
            # Restart service if it was running
            if ($wasRunning) {
                Write-Host "  Restarting service..." -ForegroundColor Yellow
                Start-Service -Name $serviceName
                Start-Sleep -Seconds 2
                Write-Host "  ✓ Service restarted" -ForegroundColor Green
            }
            
        } catch {
            Write-Host "  ✗ Error archiving log: $_" -ForegroundColor Red
            
            # Make sure service is restarted if it was stopped
            if ($wasRunning) {
                Start-Service -Name $serviceName -ErrorAction SilentlyContinue
            }
        }
    } else {
        Write-Host "  ✓ File size OK (${fileSizeMB}MB < ${MaxLogSizeMB}MB)" -ForegroundColor Green
    }
}

# Clean up old archived logs
Write-Host "`nCleaning up old archives (older than $KeepDays days)..." -ForegroundColor Cyan
$cutoffDate = (Get-Date).AddDays(-$KeepDays)
$archivedLogs = Get-ChildItem -Path $ArchiveDirectory -Filter "*.log" -File

$deletedCount = 0
foreach ($archive in $archivedLogs) {
    if ($archive.LastWriteTime -lt $cutoffDate) {
        try {
            Remove-Item -Path $archive.FullName -Force
            Write-Host "  ✓ Deleted: $($archive.Name)" -ForegroundColor Gray
            $deletedCount++
        } catch {
            Write-Host "  ✗ Error deleting: $($archive.Name) - $_" -ForegroundColor Red
        }
    }
}

if ($deletedCount -gt 0) {
    Write-Host "✓ Deleted $deletedCount old archive(s)" -ForegroundColor Green
} else {
    Write-Host "✓ No old archives to delete" -ForegroundColor Green
}

# Calculate total disk usage
$totalSizeMB = [math]::Round((Get-ChildItem -Path $LogDirectory -Recurse -File | Measure-Object -Property Length -Sum).Sum / 1MB, 2)
Write-Host "`nTotal log disk usage: ${totalSizeMB}MB" -ForegroundColor Cyan

$timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
Write-Host "[$timestamp] Log rotation complete!" -ForegroundColor Green
