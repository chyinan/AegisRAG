# AegisRAG Local Stack Startup Script
# Usage: docker compose --env-file .env -f docker/compose.yaml up -d --build

echo "Starting AegisRAG local stack..."

# Build and start services (excluding optionally heavy enterprise services)
docker compose --env-file .env -f docker/compose.yaml up -d --build \
    postgres postgres-roles redis minio migration api web worker-ingestion worker-embedding

echo ""
echo "AegisRAG services started successfully!"
echo ""
echo "Access URLs:"
echo "  - Web UI:           http://localhost:3100"
echo "  - API Health:       http://localhost:8000/health"
echo "  - API Ready:        http://localhost:8000/ready"
echo "  - MinIO Console:    http://localhost:9001 (if you want to use MinIO)"
echo "  - Prometheus:       http://localhost:9090 (monitoring dashboard)"
echo "  - Grafana:          http://localhost:3101 (admin password set in .env)"
echo "  - Jaeger UI:        http://localhost:16686 (distributed tracing)"
echo ""
echo "Waiting for services to be ready..."
sleep 5

# Initial health check
echo ""
echo "Checking service health..."
curl.exe -s http://localhost:3100/ >nul && echo "Web UI is ready!" || echo "Web UI not ready yet"
curl.exe -s http://localhost:8000/health >nul && echo "API is ready!" || echo "API not ready yet"
