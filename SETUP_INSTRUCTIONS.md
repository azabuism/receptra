# BARIYON Receptra - Local Setup Instructions

## Current Status
✅ Frontend: Ready to run at `http://localhost:5173`
✅ Backend Code: All code in place at `/Users/arkai/www/receptra/`
❌ Database: PostgreSQL not installed
❌ Docker: Docker not installed

## Next Steps Required

### Option 1: Docker Setup (Recommended - Easiest)

**Step 1: Install Docker Desktop**
1. Go to https://www.docker.com/products/docker-desktop
2. Download and install Docker Desktop for macOS (Apple Silicon or Intel)
3. Start Docker Desktop application
4. Wait for it to fully start (you'll see the Docker icon in menu bar)

**Step 2: Start Services with Docker Compose**
```bash
cd /Users/arkai/www/receptra
docker-compose up
```

This will start:
- PostgreSQL database on port 5432
- Redis cache on port 6379  
- Backend API (optional in docker-compose, we'll run separately)

**Step 3: In a NEW terminal, start the backend locally**
```bash
cd /Users/arkai/www/receptra/backend
source venv/bin/activate
pip install -r ../requirements.txt
cd ..
python -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

**Step 4: Frontend is already running**
- Visit http://localhost:5173 in your browser
- Backend will be at http://localhost:8000

---

### Option 2: Homebrew Setup (More manual)

**Step 1: Install Homebrew** (if not already installed)
```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
```

**Step 2: Install PostgreSQL**
```bash
brew install postgresql@15
brew services start postgresql@15
```

**Step 3: Create database and user**
```bash
createuser -P receptra  # Enter password: receptra
createdb -O receptra receptra
```

**Step 4: Install Redis**
```bash
brew install redis
brew services start redis
```

**Step 5: Start backend**
```bash
cd /Users/arkai/www/receptra/backend
source venv/bin/activate
cd ..
python -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

---

## Testing the Connection

Once backend is running, test with:
```bash
curl http://localhost:8000/docs
```

You should see the Swagger API documentation.

## Troubleshooting

### PostgreSQL connection failed
- Docker setup: Check `docker-compose up` output for errors
- Homebrew setup: Verify postgres is running with `brew services list`

### Port 5432 already in use (Docker)
```bash
docker ps  # See what's running
docker kill receptra-db  # Kill existing container
docker-compose up  # Start fresh
```

### Backend won't start
1. Check .env file has DATABASE_URL
2. Verify database is running and accessible
3. Check Python venv is activated in backend terminal

---

## Current Environment
- Frontend: http://localhost:5173 ✅ (Running)
- Backend: http://localhost:8000 (Ready, needs database)
- Database: Needs setup (choose Docker or Homebrew)
- API Docs: http://localhost:8000/docs (once backend starts)

---

**Recommended: Use Docker (Option 1) for fastest setup with least configuration issues.**

