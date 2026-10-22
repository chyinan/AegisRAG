# === Docker Deployment Commands (Run when network available) ===
# Copy to clipboard and run on machine with internet access

echo "Deployment Step 1: Build all services"
docker compose --env-file .env -f docker/compose.yaml \
    up -d --build postgres redis migration api web worker-ingestion worker-embedding

echo ""
echo "Deployment Step 2: Wait ~60 seconds for services to initialize..."
sleep 60

echo ""
echo "Deployment Step 3: Verify deployment:"
docker compose ps
curl http://localhost:8000/health
curl http://localhost:3100/ | head -c 100
