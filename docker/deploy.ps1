@{
# AegisRAG Docker 部署脚本
# Usage: .\deploy.ps1

param([switch]$SkipValidation, [switch]$NoRebuild)

Write-Host "=== AegisRAG Docker Deployment ===" -ForegroundColor Green
Write-Host ""

Check-PreRequisites {
    # 检查 Docker 是否可用
    if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
        Write-Host "❌ Docker not found! Please install Docker Desktop first." -ForegroundColor Red
        exit 1
}
    
    $dockerVersion = node --version 2>&1
    if ($LASTEXITCODE -eq 0) {
        Write-Host "✓ Node.js: $($nodeVersion.Trim())" -ForegroundColor Green
} else {
    Write-Host "⚠️ Node.js not found. Some features may be limited." -ForegroundColor Yellow
}
    
    # 检查 Docker Desktop 是否正在运行
    $dockerInfo = docker info 2>&1
    if ($LASTEXITCODE -ne 0) {
        Write-Host "❌ Docker Desktop is not running!" -ForegroundColor Red
        Write-Host "   Please start Docker Desktop from the Start Menu." -ForegroundColor Yellow
        exit 1
}
    
    Write-Host "✓ Docker Desktop: Running" -ForegroundColor Green
}

ValidateEnv {
    $envFile = Join-Path $PSScriptRoot "..\.env"
    
    if (-not (Test-Path $envFile)) {
        Write-Host "❌ .env file not found!" -ForegroundColor Red
        Write-Host "   Required variables:" -ForegroundColor Yellow
        Write-Host "   - JWT_SECRET, POSTGRES_PASSWORD, MINIO_SECRET_KEY" -ForegroundColor Yellow
        exit 1
}
    
    Write-Host "✓ .env configuration found" -ForegroundColor Green
    
    # 显示使用的端口映射 (脱敏显示)
    $apiPort = Get-Content ".env" | Select-String "API_PORT=" | Select-Object -First 1 | Select-String "=" | ForEach-Object{($_).Split("=")[1].Trim()}
    if ($apiPort) {
        Write-Host "   API Port: $([math]::Max(80, [int]$apiPort))" -ForegroundColor Gray
} else {
        Write-Host "   API Port: 8000 (default)" -ForegroundColor Gray
}
    
    $webPort = Get-Content ".env" | Select-String "WEB_PORT=" | Select-Object -First 1 | Select-String "=" | ForEach-Object{($_).Split("=")[1].Trim()}
    if ($webPort) {
        Write-Host "   Web Port: $([math]::Max(30, [int]$webPort))" -ForegroundColor Gray
} else {
        Write-Host "   Web Port: 3100 (default)" -ForegroundColor Gray
}}

Write-Host ""

Build-Containers {
    Write-Host "Building Docker images..." -ForegroundColor Cyan
    
    docker compose --env-file ".\env" \
        -f "\docker\compose.yaml" \
        build api web worker-ingestion worker-embedding
    
    if ($LASTEXITCODE -eq 0) {
        Write-Host "✓ Images built successfully!" -ForegroundColor Green
} else {
        if ($NoRebuild -not) {
            Write-Host "⚠️ Build failed, continuing with cached images..." -ForegroundColor Yellow
        }
    }
    
    # 清理构建缓存以提高速度 (可选，谨慎使用)
}

Start-Services {
    Write-Host "Starting services..." -ForegroundColor Cyan
    
    docker compose --env-file ".\env" \
        -f "\docker\compose.yaml" \
        up -d postgres postgres-roles redis minio migration api web worker-ingestion worker-embedding
    
    if ($LASTEXITCODE -eq 0) {
        Write-Host "✓ Services started successfully!" -ForegroundColor Green
        
        header "=== Deployment Complete ===" {}
        
        Write-Host "`nAccess URLs:`n" -ForegroundColor Cyan
        Write-Host "  🌐 Web UI:              http://localhost:{webPort}" -ForegroundColor Yellow
        Write-Host "     ${webPort ?? '3100'}" -ForegroundColor Gray
        Write-Host "  📊 API Health:          http://localhost:{apiPort}/health" -ForegroundColor Yellow
        Write-Host "     (${apiPort ?? '8000'})" -ForegroundColor Gray
        Write-Host "  ☁️ MinIO Console (opt):  http://localhost:9001" -ForegroundColor Cyan
        Write-Host "      (用户名：minioadmin)" -ForegroundColor Gray
        Write-Host "  📈 Prometheus:          http://localhost:{promPort}" -ForegroundColor Yellow
        Write-Host "     (${promPort ?? '9090'})" -ForegroundColor Gray
        Write-Host "  🎨 Grafana:             http://localhost:{grafanaPort}" -ForegroundColor Yellow
        Write-Host "     (${grafanaPort ?? '3101'} | admin: yourpwd)" -ForegroundColor Gray
        Write-Host "  🔍 Jaeger Tracing (opt): http://localhost:{jaegerPort}" -ForegroundColor Cyan
        Write-Host "     (${jaegerPort ?? '16686'})" -ForegroundColor Gray
        
        header "=== Next Steps ===" {}
        
        Write-Host "`n💡 What to do next:" -ForegroundColor Cyan
        Write-Host "  1. Open http://localhost:{webPort} in your browser" -ForegroundColor Yellow
        Write-Host "     (${webPort ?? '3100'})" -ForegroundColor Gray
        Write-Host "  2. Upload your first document via the Web UI" -ForegroundColor Cyan
        Write-Host "  3. Check logs: docker compose logs -f api" -ForegroundColor Yellow
        Write-Host ""
}
