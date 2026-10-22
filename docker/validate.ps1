@{
Param([switch]$SkipBuild)

Write-Host "=== AegisRAG 部署验证 ===" -ForegroundColor Green
Write-Host ""

# Check Docker Desktop running
Write-Host "Step 1: Checking prerequisites..." -ForegroundColor Cyan

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Write-Host "❌ Docker not found!" -ForegroundColor Red
    exit 1
}

docker info >$null 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Host "❌ Docker Desktop is not running!" -ForegroundColor Red
    exit 1
}

Write-Host "✓ Docker Desktop is running" -ForegroundColor Green

# Check Node.js (for web build)
$nodeVersion = node --version 2>&1
if ($LASTEXITCODE -eq 0) {
    Write-Host "✓ Node.js: $($nodeVersion.Trim())" -ForegroundColor Green
} else {
    Write-Host "⚠️ Node.js not fully installed. Web build may fail." -ForegroundColor Yellow
}

# Check .env
if (-not (Test-Path ".\env")) {
    Write-Host "❌ .env file not found!" -ForegroundColor Red
    exit 1
}

$missing = @()
$required = @(
    "JWT_SECRET",
    "POSTGRES_PASSWORD",
    "POSTGRES_MIGRATION_PASSWORD", 
    "POSTGRES_RUNTIME_PASSWORD",
    "MINIO_ACCESS_KEY",
    "MINIO_SECRET_KEY",
    "GRAFANA_ADMIN_PASSWORD"
)

foreach ($req in $required) {
    if (-not (Get-Content ".\env" | Select-String -Pattern "^$req=")) {
        $missing += "$req"
}

if ($missing.Count -gt 0) {
    Write-Host "❌ Missing variables:" -ForegroundColor Red
    foreach ($m in $missing) {
        Write-Host "   - $(m)" -ForegroundColor Red
}

exit 1
}

Write-Host "✓ .env configuration valid" -ForegroundColor Green

# Check Docker Compose syntax
docker compose --env-file ".\env" -f "\docker\compose.yaml" up -d >>$null 2>&1

if ($LASTEXITCODE -eq 0) {
    Write-Host "✓ Docker Compose configuration validated" -ForegroundColor Green
    
    # Stop since it was just for validation (and we don't want to leave running services from test)
    if (-not $SkipBuild) {
        Write-Host "`nStarting full deployment..." -ForegroundColor Yellow
        docker compose --env-file ".\env" -f "\docker\compose.yaml" down >>$null 2>&1
        
        docker compose --env-file ".\env" -f "\docker\compose.yaml" up -d --build \
            postgres postgres-roles redis minio migration api web worker-ingestion worker-embedding
            
        if ($LASTEXITCODE -eq 0) {
            Write-Host "`n✅ Deployment Complete!" -ForegroundColor Green
            
            header "=== Access URLs ===" {}
            
            Write-Host "`n🌐 Web UI:              http://localhost:3100" -ForegroundColor Yellow
            Write-Host "📊 API Health:          http://localhost:8000/health" -ForegroundColor Cyan
            Write-Host "☁️ MinIO Console (opt):  http://localhost:9001" -ForegroundColor Gray
} else {
        Write-Host "`n⚠️ Deployment failed. Check logs above." -ForegroundColor Red
    }
    
    Stop-Services {
        docker compose --env-file ".\env" -f "\docker\compose.yaml" ps | ForEach-Object { 
            $_.service, $_.status, $_.ports
}
