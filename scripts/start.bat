@echo off
REM ============================================================
REM AegisRAG Local Stack Startup Script for Windows
REM Requirements: Docker Desktop installed and running
REM ============================================================

echo ================================================
echo Starting AegisRAG Local Stack...
echo ================================================
echo.

REM Build and start all services
docker compose --env-file .env -f docker/compose.yaml up -d --build postgres postgres-roles redis minio migration api web worker-ingestion worker-embedding

if %ERRORLEVEL% EQU 0 (
    echo.
    echo ^[SUCCESS^] AegisRAG services started successfully!
    echo.
    echo Access URLs:
    echo   - Web UI:              http://localhost:3100
    echo   - API Health Check:    http://localhost:8000/health
    echo   - API Ready Check:     http://localhost:8000/ready
    echo   - MinIO Console:       http://localhost:9001^ (optional)
    echo   - Prometheus:          http://localhost:9090^ (monitoring)
    echo   - Grafana:             http://localhost:3101^ (admin password in .env)
    echo   - Jaeger Tracing:      http://localhost:16686
    echo.
    echo Waiting for services to be fully initialized...
    timeout /t 10 /nobreak >nul
    
    echo.
    echo Checking service health...
    
    curl.exe -s http://localhost:3100/ >nul 2>&1 && echo   [OK] Web UI is ready! || echo   [WARN] Web UI not ready yet (normal on first start)
    curl.exe -s http://localhost:8000/health >nul 2>&1 && echo   [OK] API is ready! || echo   [WARN] API not ready yet (normal on first start)
    
    echo.
    echo ================================================
    echo IMPORTANT SECURITY REMINDERS:
    echo ================================================
    echo 1. Please change the following in .env before production use:
    echo    - JWT_SECRET
    echo    - POSTGRES_PASSWORD, POSTGRES_MIGRATION_PASSWORD, POSTGRES_RUNTIME_PASSWORD
    echo    - MINIO_SECRET_KEY
    echo    - GRAFANA_ADMIN_PASSWORD
    
    echo.
    echo 2. Initial data seeding (optional):
    echo    Run: docker compose --env-file .env exec web yarn prisma db seed
    echo    Or check if there's a seeds script in apps/web/
    
    echo.
    echo 3. To stop all services, run: start-stop.bat
    
    SET EXIT_CODE=0
) else (
    echo.
    echo ^[ERROR^] Failed to start AegisRAG services.
    echo Please check the following:
    echo   - Docker Desktop is running
    echo   - .env file is properly configured
    echo   - Port 3100, 8000, 5432, etc. are not blocked by firewall
)

exit %EXIT_CODE%
