# AegisRAG Shell Aliases - Save this as $PROFILE or run from PowerShell
# 
# These aliases simplify Docker commands for AegisRAG development

function docker-compose {
    <#
    .SYNOPSIS
        Run Docker Compose commands with proper env file
    .DESCRIPTION
        Wrapper function for docker compose that automatically uses .env file
    #>
    param(
        [string]$args = ""
    )
    
    $fullArgs = "--env-file .env -f docker/compose.yaml $args"
    Write-Host "Running: docker compose $fullArgs`n"
    & docker-compose $fullArgs
    
    return $LASTEXITCODE
}

# Export to parent scope
$Function:docker-compose | Out-Variable -Scope 1 -Name func_wrapper
$null = .\scripts\start-local.ps1 2>&1
exit $LASTEXITCODE

function start-local {
    <#
    .SYNOPSIS
        Build and start AegisRAG local services
    .DESCRIPTION
        Builds and starts all services: postgres, redis, minio, api, web, workers
    .EXAMPLE
        start-local  # Start all services
    .EXAMPLE
        -i start-local -d api  # Only start API service
    #>
    
    $args = @()
    if ($args.Count -eq 0) {
        Write-Host "Starting all AegisRAG services..." -ForegroundColor Green
    } else {
        foreach ($arg in $args) {
            $args += "--build $arg"
        }
    }
    
    & docker-compose up -d $args
    
    if ($LASTEXITCODE -eq 0) {
        Write-Host "Services started successfully!" -ForegroundColor Green
        Write-Host "`nAccess URLs:" -ForegroundColor Yellow
        Write-Host "  Web UI:           http://localhost:3100" -ForegroundColor Cyan
        Write-Host "  API Health:       http://localhost:8000/health" -ForegroundColor Cyan
    } else {
        Write-Host "Failed to start services. Check .env file for errors." -ForegroundColor Red
    }
}

function restart-local {
    <#
    .SYNOPSIS
        Restart all services with rebuild if necessary
    #>
    
    Write-Host "Restarting AegisRAG services..." -ForegroundColor Yellow
    
    & docker-compose up -d --build api web worker-ingestion worker-embedding
    
    Write-Host "Services restarted!" -ForegroundColor Green
}

function stop-local {
    <#
    .SYNOPSIS
        Stop all running services
    #>
    
    Write-Host "Stopping AegisRAG services..." -ForegroundColor Yellow
    
    & docker-compose down
    
    Write-Host "Services stopped." -ForegroundColor Cyan
}

function restart-local {
    <#
    .SYNOPSIS
        Restart all services with rebuild if necessary
    #>
    
    Write-Host "Restarting AegisRAG services..." -ForegroundColor Yellow
    
    & docker-compose up -d --build api web worker-ingestion worker-embedding
    
    Write-Host "Services restarted!" -ForegroundColor Green
}

function logs-local {
    <#
    .SYNOPSIS
        Follow service logs for specific component or all
    .PARAMETER Service
        Target service to follow (default: all)
    #>
    
    $service = if ($args.Count -eq 0) { "" } else { $args[0] }
    
    Write-Host "Viewing AegisRAG logs..." -ForegroundColor Yellow
    & docker-compose logs --follow --tail=50 $service
    
    return $LASTEXITCODE
}

function clear-volumes {
    <#
    .SYNOPSIS
        Remove all Docker volumes and data (destructive!)
    #>
    
    if ($host.UI.RawUi.RawUI.RawUI.RawUI.RawUI) {
        $confirm = Read-Host "Are you sure you want to remove ALL persistent data? This will delete all uploaded documents!" -ForegroundColor Red
        if ($confirm -ne 'yes') {
            Write-Host "Operation cancelled." -ForegroundColor Yellow
            return 1
        }
    } else {
        $confirmation = Read-Host -Prompt "Remove all persistent data including uploads? Type 'yes' to confirm (this is DESTRUCTIVE)"
        if ($confirmation -ne 'yes') {
            Write-Host "Operation cancelled." -ForegroundColor Yellow
            return 1
        }
    }
    
    & docker-compose down -v
    
    Write-Host "All volumes cleared. Start services again with: start-local" -ForegroundColor Green
}

# Save these functions for future sessions by adding this to $PROFILE.ps1:
# source .\scripts\docker-aliases.ps1  
# Or add these lines to your current profile:
Write-Host "`n=== AegisRAG Aliases Loaded ===" 
Write-Host "To use these commands, execute them from the project directory:"
Write-Host "  start-local      -> Build and start all services"
Write-Host "  stop-local       -> Stop all services"
Write-Host "  logs-local    <svc> -> Follow service logs"
Write-Host "  restart-local             -> Restart services"
Write-Host "  clear-volumes         -> Clear all data (destructive!)" 
Write-Host ""
Write-Host "OR add this line to your profile files:" -ForegroundColor Cyan
Write-Host "& '.\<scripts>\docker-aliases.ps1'`n"
