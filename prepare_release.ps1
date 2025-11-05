# Docker Pilot - Release Preparation Script for Windows PowerShell
# This script prepares the repository for GitHub release

Write-Host ""
Write-Host "======================================" -ForegroundColor Cyan
Write-Host " Docker Pilot - Release Preparation" -ForegroundColor Cyan
Write-Host "======================================" -ForegroundColor Cyan
Write-Host ""

# Check if git is initialized
if (-not (Test-Path ".git")) {
    Write-Host "[WARNING] Git repository not initialized" -ForegroundColor Yellow
    Write-Host "Initializing git repository..." -ForegroundColor Yellow
    git init
    Write-Host "[OK] Git repository initialized" -ForegroundColor Green
}

# Check for unwanted files
Write-Host ""
Write-Host "[*] Checking for unwanted files..." -ForegroundColor Yellow
$unwantedFiles = @()

# Check for log files
if (Test-Path "src\dockerpilot\docker_pilot.log") {
    $unwantedFiles += "src\dockerpilot\docker_pilot.log"
}

# Check for cache directories
if (Test-Path "__pycache__") {
    $unwantedFiles += "__pycache__"
}

if ($unwantedFiles.Count -gt 0) {
    Write-Host "[WARNING] Found files that should be removed:" -ForegroundColor Yellow
    foreach ($file in $unwantedFiles) {
        Write-Host "   - $file" -ForegroundColor White
    }
    Write-Host ""
    $remove = Read-Host "Remove these files from git tracking? (y/N)"
    if ($remove -eq "y" -or $remove -eq "Y") {
        foreach ($file in $unwantedFiles) {
            if (Test-Path $file) {
                git rm --cached $file 2>$null
                if ($LASTEXITCODE -eq 0) {
                    Write-Host "[OK] Removed from git: $file" -ForegroundColor Green
                } else {
                    Write-Host "[INFO] File not tracked: $file" -ForegroundColor Gray
                }
            }
        }
        Write-Host "[OK] Files removed from git tracking" -ForegroundColor Green
    }
} else {
    Write-Host "[OK] No unwanted files found" -ForegroundColor Green
}

# Check for sensitive data
Write-Host ""
Write-Host "[*] Checking for sensitive data..." -ForegroundColor Yellow
$sensitivePatterns = @("password", "secret", "api_key", "token", "credential")
$foundSensitive = $false

foreach ($pattern in $sensitivePatterns) {
    $matches = Get-ChildItem -Recurse -Include *.py,*.yml,*.yaml -Exclude *.template | 
        Select-String -Pattern $pattern -CaseSensitive:$false | 
        Where-Object { $_.Line -notmatch "credentials|password|token|template|example|#.*$pattern" }
    
    if ($matches) {
        Write-Host "[WARNING] Found potential sensitive data: $pattern" -ForegroundColor Yellow
        $foundSensitive = $true
    }
}

if (-not $foundSensitive) {
    Write-Host "[OK] No sensitive data found" -ForegroundColor Green
}

# Check file sizes
Write-Host ""
Write-Host "[*] Checking for large files..." -ForegroundColor Yellow
$largeFiles = Get-ChildItem -Recurse -File | 
    Where-Object { $_.Length -gt 1MB -and $_.FullName -notmatch "\.git\\|\.venv\\|venv\\" }

if (-not $largeFiles) {
    Write-Host "[OK] No large files found" -ForegroundColor Green
} else {
    Write-Host "[WARNING] Found large files:" -ForegroundColor Yellow
    foreach ($file in $largeFiles) {
        Write-Host "   - $($file.FullName) ($([math]::Round($file.Length/1MB, 2)) MB)" -ForegroundColor White
    }
}

# Summary
Write-Host ""
Write-Host "======================================" -ForegroundColor Cyan
Write-Host "[OK] Release preparation complete!" -ForegroundColor Green
Write-Host ""
Write-Host "Next steps:" -ForegroundColor Cyan
Write-Host "  1. Review changes: git status" -ForegroundColor White
Write-Host "  2. Add files: git add ." -ForegroundColor White
Write-Host "  3. Commit: git commit -m 'Initial release: Docker Pilot v0.1.0'" -ForegroundColor White
Write-Host "  4. Add remote: git remote add origin https://github.com/DozeyUDK/DockerPilot.git" -ForegroundColor White
Write-Host "  5. Push: git push -u origin main" -ForegroundColor White
Write-Host ""

