# RECEPTRA - Restaurant Reservation System with Twilio Voice

A complete restaurant reservation system with voice-based IVR (Interactive Voice Response) and automated reminder calls powered by Twilio.

## 📋 Project Overview

RECEPTRA is a FastAPI-based backend system that enables:

✅ **Phone-based Reservations**
- Customers call a Twilio phone number
- Interactive voice system guides them through the booking process
- Collects: name, date, time, party size
- Confirms reservation and provides confirmation

✅ **Automated Reminders**
- Scheduled reminder calls the day before reservation
- Customers can confirm or request modifications
- Reduces no-show rates

✅ **REST API**
- Full HTTP API for managing reservations
- Check reservation status
- Create/modify/cancel reservations programmatically

✅ **Local Development Ready**
- SQLite database (easily upgradable to PostgreSQL)
- Test phone number included (no Japan regulatory approvals needed)
- Development server with hot reload

---

## 🏗️ Project Structure

```
receptra/
├── venv/                    # Python virtual environment (3.11.15)
├── .env                     # Twilio credentials & configuration
├── requirements.txt         # Python dependencies
├── receptra_backend.py      # FastAPI application (850+ lines)
├── receptra.db             # SQLite database
├── start_server.sh          # Server startup script
├── test_setup.py            # Setup verification script
├── QUICK_START.md           # Detailed setup guide
└── README.md               # This file
```

---

## 🚀 Quick Start

### 1. **Start the Development Server**

```bash
cd ~/receptra
./start_server.sh
```

Or manually:
```bash
cd ~/receptra
source venv/bin/activate
uvicorn receptra_backend:app --reload --host 0.0.0.0 --port 8000
```

### 2. **Access API Documentation**

Open your browser:
- **Interactive API Docs (Swagger):** http://localhost:8000/docs
- **Alternative Docs (ReDoc):** http://localhost:8000/redoc
- **Health Check:** http://localhost:8000/health

### 3. **Create a Test Reservation**

```bash
curl -X POST http://localhost:8000/reservations \
  -H "Content-Type: application/json" \
  -d '{
    "phone_number": "+819012345678",
    "customer_name": "山田太郎",
    "reservation_date": "2026-09-20T19:00:00",
    "party_size": 4,
    "special_requests": "アレルギー対応不要"
  }'
```

### 4. **View All Reservations**

```bash
curl http://localhost:8000/reservations
```

---

## 📞 IVR Flow Diagram

```
[Incoming Call]
       ↓
[Greeting] → "いらっしゃいませ"
       ↓
[Get Name] → "お名前をお聞きします"
       ↓
[Get Date] → "ご予約希望日は？"
       ↓
[Get Time] → "ご予約希望時間は？"
       ↓
[Get Party Size] → "ご来店人数は？"
       ↓
[Confirm] → Summary of booking + Confirm (1) or Modify (2)?
       ↓
[Save] → Create reservation + Schedule reminder
       ↓
[Hangup] → "ご予約ありがとうございます"
```

---

## 🔧 Configuration

### Environment Variables (`.env`)

```env
TWILIO_ACCOUNT_SID=ACe95d93e67c5a6449bf3ac320f94fdf72    # Trial account
TWILIO_AUTH_TOKEN=d0c1fd6f867d20e52f52f5b9               # Auth token
TWILIO_PHONE_NUMBER=+81312345678                         # Test phone number
API_HOST=http://localhost:8000                           # Callback URL
DATABASE_PATH=receptra.db                                 # SQLite database
```

**Note:** Using test phone number (+81312345678) to avoid Japan regulatory requirements. Upgradeable to real number when ready.

---

## 📚 API Endpoints

### Health & Status
- `GET /health` - Health check

### Incoming Calls (IVR)
- `POST /call/incoming` - Handle incoming call
- `POST /call/process_name` - Process name input
- `POST /call/process_date` - Process date input
- `POST /call/process_time` - Process time input
- `POST /call/process_party_size` - Process party size input
- `POST /call/process_confirmation` - Confirm reservation

### Reminders
- `POST /call/reminder/{reservation_id}` - Send reminder call
- `POST /call/reminder_response/{reservation_id}` - Process reminder response

### Reservation Management
- `GET /reservations` - List all reservations
- `GET /reservations/{id}` - Get reservation details
- `POST /reservations` - Create reservation
- `DELETE /reservations/{id}` - Cancel reservation

### Interactive Documentation
- `GET /docs` - Swagger UI
- `GET/redoc` - ReDoc documentation

---

## 🛠️ Technology Stack

| Component | Version | Purpose |
|-----------|---------|---------|
| **FastAPI** | 0.104.1 | Web framework |
| **Uvicorn** | 0.24.0 | ASGI server |
| **Twilio** | 8.10.0 | Voice API |
| **SQLAlchemy** | 2.0.23 | ORM |
| **APScheduler** | 3.10.4 | Task scheduling |
| **Pydantic** | 2.5.0 | Data validation |
| **Python** | 3.11.15 | Runtime |
| **SQLite** | - | Database |

---

## 🔌 Integrating with Twilio

### 1. Set up ngrok Tunnel

```bash
# In a separate terminal
ngrok http 8000
```

Output example:
```
Forwarding                    https://1a2b3c4d5e6f.ngrok.io -> http://localhost:8000
```

### 2. Configure Twilio Webhook

1. Go to [Twilio Console](https://console.twilio.com)
2. Navigate to **Phone Numbers** → **Manage Numbers**
3. Select your test number
4. Under **Voice Configuration**, set:
   - **A Call Comes In:** `https://1a2b3c4d5e6f.ngrok.io/call/incoming`
   - **Primary Handler:** Post-Request
5. Click **Save**

### 3. Test the System

Call your Twilio number from any phone and follow the voice prompts!

---

## 📊 Database

### Reservation Table

```sql
CREATE TABLE reservations (
    id INTEGER PRIMARY KEY,
    phone_number VARCHAR NOT NULL,
    customer_name VARCHAR NOT NULL,
    reservation_date DATETIME NOT NULL,
    party_size INTEGER NOT NULL,
    special_requests VARCHAR,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    reminder_sent INTEGER DEFAULT 0,
    call_responses JSON
);
```

### Query Examples

```bash
# View database with SQLite CLI
sqlite3 receptra.db

# Inside SQLite CLI:
sqlite> SELECT * FROM reservations;
sqlite> SELECT customer_name, reservation_date FROM reservations WHERE reminder_sent = 0;
sqlite> .exit
```

---

## 🧪 Testing

### Run Setup Verification

```bash
cd ~/receptra
source venv/bin/activate
python test_setup.py
```

### Manual API Testing

```bash
# Health check
curl http://localhost:8000/health

# Get all reservations
curl http://localhost:8000/reservations

# Get specific reservation
curl http://localhost:8000/reservations/1

# Create reservation (from quick start)
curl -X POST http://localhost:8000/reservations \
  -H "Content-Type: application/json" \
  -d '{"phone_number": "+819012345678", "customer_name": "太郎", "reservation_date": "2026-09-20T19:00:00", "party_size": 4}'
```

---

## 🐛 Troubleshooting

### Server won't start
```bash
# Check if port 8000 is in use
lsof -i :8000

# Kill the process
kill -9 <PID>
```

### Database locked error
```bash
# Remove corrupted database
rm receptra.db

# Restart server (new database will be created)
```

### Twilio connection issues
- Verify `.env` credentials are correct
- Check that ngrok tunnel is running
- Confirm Twilio webhook URL is configured
- Check that webhook URL uses HTTPS (http://... will be rejected)

### Call transcription issues
- Ensure microphone is working
- Speak clearly
- Allow proper timeout between prompts

---

## 📈 Next Steps

### Phase 1: Core Features (✅ Complete)
- [x] IVR voice system
- [x] Reservation database
- [x] REST API
- [x] Reminder scheduling

### Phase 2: Frontend Integration
- [ ] Web dashboard for reservations
- [ ] Reservation confirmation page
- [ ] Customer history
- [ ] Receipt/confirmation email

### Phase 3: Advanced Features
- [ ] SMS notifications
- [ ] Payment integration
- [ ] Multi-language support
- [ ] Restaurant management dashboard
- [ ] Analytics & reporting

### Phase 4: Production Deployment
- [ ] Real Japan phone number
- [ ] HTTPS/SSL
- [ ] PostgreSQL database
- [ ] Docker containerization
- [ ] Cloud deployment (AWS/GCP/Azure)

---

## 📖 Documentation

- **[QUICK_START.md](QUICK_START.md)** - Detailed setup and configuration guide
- **[OpenAPI Spec](http://localhost:8000/openapi.json)** - Machine-readable API specification
- **[Twilio Docs](https://www.twilio.com/docs)** - Official Twilio documentation
- **[FastAPI Docs](https://fastapi.tiangolo.com)** - FastAPI framework guide

---

## 📝 License

This project is for educational and development purposes.

---

## 👨‍💻 Support

For issues or questions:

1. Check [QUICK_START.md](QUICK_START.md) troubleshooting section
2. Review API documentation at http://localhost:8000/docs
3. Check [Twilio Status](https://status.twilio.com)
4. Review server logs in terminal where uvicorn is running

---

## 🎯 Key Features Implemented

### ✅ Voice IVR System
- Natural language voice prompts (Japanese)
- DTMF input collection
- State management for multi-turn conversations
- Graceful error handling

### ✅ Reservation Management
- SQLAlchemy ORM for database operations
- Automatic timestamp tracking
- JSON storage for call metadata
- Soft delete capability

### ✅ Automated Reminders
- APScheduler for background task scheduling
- Configurable reminder times (default: 14:00 JST, day before)
- Automatic retry on call failure
- Reminder status tracking

### ✅ REST API
- Full CRUD operations for reservations
- Swagger UI auto-documentation
- Pydantic model validation
- HTTP status code compliance

### ✅ Development Ready
- Hot reload on code changes
- Comprehensive logging
- Local SQLite database
- Test phone number (no approval needed)

---

**Version:** 1.0.0  
**Last Updated:** 2026-09-10  
**Status:** ✅ Ready for Development
