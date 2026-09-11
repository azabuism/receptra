#!/bin/bash
# BARIYON Receptra - Quick Start Script for macOS

echo "🚀 BARIYON Receptra - Quick Start"
echo "=================================="
echo ""

# Check current directory
cd /Users/arkai/www/receptra || { echo "❌ Project directory not found"; exit 1; }

echo "✅ Project directory confirmed: $(pwd)"
echo ""

# Check for Docker
if ! command -v docker &> /dev/null; then
    echo "⚠️  Docker is not installed"
    echo ""
    echo "To proceed, you need to:"
    echo "1. Install Docker Desktop from https://www.docker.com/products/docker-desktop"
    echo "2. Start Docker Desktop application"
    echo "3. Run this script again"
    echo ""
    echo "Full setup instructions available in: SETUP_INSTRUCTIONS.md"
    exit 1
fi

echo "✅ Docker found: $(docker --version)"
echo ""

# Check if docker daemon is running
if ! docker ps &> /dev/null; then
    echo "❌ Docker daemon is not running"
    echo "Please start Docker Desktop application and try again"
    exit 1
fi

echo "✅ Docker daemon is running"
echo ""

# Start database and cache services
echo "🔄 Starting PostgreSQL and Redis with Docker Compose..."
echo "(This may take a minute on first run)"
echo ""

docker-compose up -d

sleep 3

# Check if services started
if docker-compose ps | grep -q "healthy"; then
    echo "✅ Database services started successfully"
else
    echo "⚠️  Waiting for services to be healthy..."
    sleep 5
fi

echo ""
echo "📋 Next Steps:"
echo "=============="
echo ""
echo "1️⃣  START BACKEND (in a new terminal):"
echo "   cd /Users/arkai/www/receptra/backend"
echo "   source venv/bin/activate"
echo "   cd .."
echo "   python -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000"
echo ""
echo "2️⃣  START FRONTEND (in another terminal):"
echo "   cd /Users/arkai/www/receptra/frontend"
echo "   npm run dev"
echo ""
echo "3️⃣  OPEN BROWSER:"
echo "   Frontend: http://localhost:5173"
echo "   Backend API Docs: http://localhost:8000/docs"
echo ""
echo "✅ Database services are running in the background"
echo "   PostgreSQL: localhost:5432"
echo "   Redis: localhost:6379"
echo ""
echo "To stop all services later, run: docker-compose down"
echo ""
