@echo off
REM ============================================================
REM AegisRAG Local Stack Stop Script for Windows
REM ============================================================

echo ================================================
echo Stopping AegisRAG Local Stack...
echo ================================================
echo.

docker compose --env-file .env -f docker/compose.yaml down

if %ERRORLEVEL% EQU 0 (
    echo.
    echo ^[OK^] All services stopped successfully!
) else (
    echo.
    echo ^[WARN^] Could not stop all services (some may still be running).
)

echo.
type nul >nul 2>&1 && docker compose --env-file .env -f docker/compose.yaml ps || echo "No running services found."
