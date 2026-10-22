@echo off
REM ============================================================
REM AegisRAG Simplified Local Deployment (No MinIO)
REM Uses local file system instead of S3-compatible storage
REM ============================================================

echo ================================================
echo AegisRAG - Simplified Docker 部署 (无 MinIO 方案)
echo ================================================
echo.

echo Step 1: Creating local.env from .env.example...
if exist ".\.env" (
    echo ✓ .env file already exists
) else if exist ".\env.example" (
    copy /Y ".\env.example" ".\env"
    echo ✓ Created .env file from template
) else (
    echo ✗ ERROR: No .env or .env.example found!
    echo Please create .env file first with required variables.
    exit /b 1
)

echo.
echo Step 2: Verifying configuration...

REM Check if MinIO settings are disabled in env
set MINIO_DISABLED=0
if not defined MINIO_ENABLED (
    set MINIO_ENABLED=false
)

if "%MINIO_ENABLED%"=="false" (
    echo ✓ Using local file storage instead of MinIO
) else (
    echo ⚠️  Warning: MINIO_ENABLED is true but MinIO images are unavailable
    echo Proceeding with degraded mode
)

echo.
echo Step 3: Building and starting services...
echo.
echo This will take about 5-10 minutes for initial build.
echo.

REM First, stop any existing containers to ensure clean start
docker compose --env-file .env -f docker/compose.yaml down >nul 2>&1

REM Build all services with the local config
docker compose --env-file .env -f docker/compose.yaml up -d --build \
    postgres postgres-roles redis migration api web worker-ingestion worker-embedding

if %ERRORLEVEL% EQU 0 (
    echo.
    echo ================================================
    echo ✓ Deployment complete!
    echo ================================================
    echo.
    echo Access URLs:
    echo   - Web UI (Next.js):          http://localhost:3100
    echo   - API Status:                http://localhost:8000/health
    echo   - Prometheus:                 http://localhost:9090^ (optional)
    echo   - Grafana:                   http://localhost:3101^ (optional, admin password in .env)
    echo.
    echo Document storage location: %LOCAL_FILE_STORAGE%-\ingested-documents- (host machine)
    echo.
    echo To check service status after 60 seconds:
    echo   docker compose ps
    echo.
    echo For documentation see: docs\DEPLOYMENT_SUMMARY.md
) else (
    echo.
    echo ✗ Deployment failed! Check .env file and logs above.
)

echo.
pause
