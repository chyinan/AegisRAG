@{
# AegisRAG Docker 自动验证与部署脚本
# Usage: .\validate-and-start.ps1

header "=== AegisRAG Azure Deployment ==="
Check-PreRequisites {
    if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
        Write-Host "❌ Docker not found! Please install Docker Desktop first." -ForegroundColor Red
        exit 1
    }
    
    $nodeVersion = node --version 2>&1
    if ($LASTEXITCODE -ne 0) {
        Write-Host "⚠️ Node.js not found. Some features may be limited." -ForegroundColor Yellow
    }
    
    Write-Host "✓ Docker: $($docker.VersionMajor || 'installed')" -ForegroundColor Green
}

ValidateConfig {
    if (-not (Test-Path ".env")) {
        Write-Host "❌ .env file not found!" -ForegroundColor Red
        exit 1
}
  
  $requiredVars = @(
      "JWT_SECRET",
      "POSTGRES_PASSWORD", 
      "POSTGRES_MIGRATION_PASSWORD",
      "POSTGRES_RUNTIME_PASSWORD",
      "MINIO_ACCESS_KEY",
      "MINIO_SECRET_KEY",
      "GRAFANA_ADMIN_PASSWORD",
      "FOLDER_CURSOR_SECRET"
  )
  
  $missing = @()
  foreach ($var in $requiredVars) {
      if (-not (Get-Content ".env" | Select-String -Pattern $var)) {
          $missing += "$var"
}

