# Receptra API - File Locations and Structure

## Project Root
```
/home/claude/
```

## Models (SQLAlchemy ORM)
All models are located in `/home/claude/app/models/`

| File | Purpose | Key Classes |
|------|---------|-------------|
| `shop.py` | Shop and operating hours models | `Shop`, `ShopHours` |
| `customer.py` | Customer profile model | `Customer` |
| `reservation.py` | Reservation booking model | `Reservation` |
| `user.py` | User authentication model | `User`, `Tenant` |
| `receptionist.py` | Receptionist role model | `Receptionist` |
| `visitor.py` | Visitor/guest model | `Visitor` |
| `__init__.py` | Model exports | Imports all models for ORM |

**Total: 3 new model files created**

## Schemas (Pydantic Validation)
All request/response schemas in `/home/claude/app/schemas/`

| File | Purpose | Key Classes |
|------|---------|-------------|
| `shop.py` | Shop validation and responses | `ShopRegisterRequest`, `ShopResponse`, `ShopSearchResponse`, `ShopSearchQuery` |
| `customer.py` | Customer validation and responses | `CustomerRegisterRequest`, `CustomerResponse`, `CustomerUpdateRequest` |
| `reservation.py` | Reservation validation and responses | `ReservationCreateRequest`, `ReservationResponse`, `ReservationUpdateRequest` |
| `receptionist.py` | Receptionist schemas | Existing schemas |
| `user.py` | User schemas | Existing schemas |
| `visitor.py` | Visitor schemas | Existing schemas |
| `reports.py` | Reporting schemas | Existing schemas |
| `__init__.py` | Schema exports | Imports all schemas |

**Total: 3 new schema files created**

## Routers (API Endpoints)
All route handlers in `/home/claude/app/routers/`

| File | Purpose | Endpoints | Status |
|------|---------|-----------|--------|
| `shops.py` | Shop management endpoints | POST /api/v1/shops/register, GET /api/v1/shops/search, GET /api/v1/shops/{id} | ✓ NEW |
| `customers.py` | Customer management endpoints | POST /api/v1/customers/register, GET /api/v1/customers/{id}, GET /api/v1/customers/search/by-email, PUT /api/v1/customers/{id} | ✓ NEW |
| `reservations.py` | Reservation management endpoints | POST /api/v1/reservations/create, GET /api/v1/reservations/{id}, GET /api/v1/reservations/shop/{id}, PUT /api/v1/reservations/{id}, DELETE /api/v1/reservations/{id} | ✓ NEW |
| `auth.py` | Authentication | POST /auth/register, POST /auth/login | Existing |
| `users.py` | User management | Existing endpoints | Existing |
| `receptionists.py` | Receptionist management | Existing endpoints | Existing |
| `visitors.py` | Visitor management | Existing endpoints | Existing |
| `reports.py` | Reporting | Existing endpoints | Existing |
| `__init__.py` | Router exports | Imports all routers | ✓ UPDATED |

**Total: 3 new router files created**

## Core Application Files
```
/home/claude/app/
├── main.py              - Main FastAPI application (UPDATED with new routers)
├── database.py          - Database setup and session management
├── dependencies.py      - JWT extraction dependency (NEW)
├── config.py            - Configuration if exists
└── services/            - Business logic services
    └── user.py          - User service functions
```

## Support Files
```
/home/claude/
├── requirements.txt     - Python dependencies (UPDATED PyJWT version)
├── test_apis.py        - Comprehensive API test suite (CREATED)
├── README.md           - Project documentation
├── PROJECT_STRUCTURE.txt - Project structure reference
└── API docs/           - OpenAPI/Swagger docs (auto-generated)
```

## Database
```
PostgreSQL 16
  Host: localhost
  Port: 5432
  Database: receptra_db
  User: receptra_user
  Password: receptra_password
```

## Key Directories

### Models Directory
```
/home/claude/app/models/
├── __init__.py          - Exports: User, Tenant, Receptionist, Visitor, Shop, ShopHours, Customer, Reservation
├── user.py              - Tenant and User models
├── receptionist.py      - Receptionist model
├── visitor.py           - Visitor model
├── shop.py              - Shop and ShopHours models (NEW)
├── customer.py          - Customer model (NEW)
└── reservation.py       - Reservation model (NEW)
```

### Schemas Directory
```
/home/claude/app/schemas/
├── __init__.py          - Exports all schema classes
├── user.py              - User/Tenant schemas
├── receptionist.py      - Receptionist schemas
├── visitor.py           - Visitor schemas
├── reports.py           - Report schemas
├── shop.py              - Shop schemas (NEW)
│   └── Classes:
│       - ShopHoursCreate
│       - ShopHoursResponse
│       - ShopRegisterRequest
│       - ShopResponse
│       - ShopSearchResponse
│       - ShopSearchQuery
│       - ShopListResponse
├── customer.py          - Customer schemas (NEW)
│   └── Classes:
│       - CustomerRegisterRequest
│       - CustomerUpdateRequest
│       - CustomerResponse
│       - CustomerRegisterResponse
│       - CustomerListResponse
└── reservation.py       - Reservation schemas (NEW)
    └── Classes:
        - ReservationCreateRequest
        - ReservationUpdateRequest
        - ReservationResponse
        - ReservationCreateResponse
        - ReservationListResponse
```

### Routers Directory
```
/home/claude/app/routers/
├── __init__.py          - Exports all routers
├── auth.py              - Authentication routes
├── users.py             - User management routes
├── receptionists.py     - Receptionist management routes
├── visitors.py          - Visitor management routes
├── reports.py           - Reporting routes
├── shops.py             - Shop management routes (NEW)
│   └── Endpoints:
│       - POST /api/v1/shops/register
│       - GET /api/v1/shops/search
│       - GET /api/v1/shops/{shop_id}
├── customers.py         - Customer management routes (NEW)
│   └── Endpoints:
│       - POST /api/v1/customers/register
│       - GET /api/v1/customers/{customer_id}
│       - GET /api/v1/customers/search/by-email
│       - PUT /api/v1/customers/{customer_id}
└── reservations.py      - Reservation management routes (NEW)
    └── Endpoints:
        - POST /api/v1/reservations/create
        - GET /api/v1/reservations/{reservation_id}
        - GET /api/v1/reservations/shop/{shop_id}
        - PUT /api/v1/reservations/{reservation_id}
        - DELETE /api/v1/reservations/{reservation_id}
```

## Configuration Files

### requirements.txt
```
Location: /home/claude/requirements.txt
Changes: Updated PyJWT from 2.8.1 (non-existent) to 2.8.0

Contains:
- fastapi==0.104.1
- uvicorn[standard]==0.24.0
- sqlalchemy==2.0.23
- asyncpg==0.29.0
- pydantic==2.5.0
- pydantic[email]==2.5.0
- PyJWT==2.8.0 (UPDATED)
- argon2-cffi==23.1.0
- python-multipart==0.0.6
```

### Installed and Running
- PostgreSQL 16 (service: postgresql)
- Python 3.11
- Virtual environment packages installed via pip

## Test Files
```
/home/claude/test_apis.py      - Comprehensive test suite
                                - Tests all 3 primary APIs
                                - Tests customer search
                                - Tests reservation CRUD
                                - All tests passing ✓
```

## Output Documentation
```
/mnt/user-data/outputs/
├── API_IMPLEMENTATION_SUMMARY.md   - Detailed implementation guide
├── API_QUICK_REFERENCE.md          - Quick API reference with curl examples
└── FILE_LOCATIONS.md               - This file
```

## Summary of Changes

### New Files Created: 8
- `/app/models/shop.py`
- `/app/models/customer.py`
- `/app/models/reservation.py`
- `/app/schemas/shop.py`
- `/app/schemas/customer.py`
- `/app/schemas/reservation.py`
- `/app/routers/shops.py`
- `/app/routers/customers.py`
- `/app/routers/reservations.py` (9 new files)
- `/app/dependencies.py`

### Files Updated: 5
- `/app/models/__init__.py` - Added new model exports
- `/app/schemas/__init__.py` - Added new schema exports
- `/app/routers/__init__.py` - Added new router exports
- `/app/main.py` - Added new router registrations and imports
- `/requirements.txt` - Fixed PyJWT version

### Test Files Created: 1
- `/test_apis.py` - Complete test suite

### Documentation Created: 3
- `API_IMPLEMENTATION_SUMMARY.md`
- `API_QUICK_REFERENCE.md`
- `FILE_LOCATIONS.md`

## Running the Application

### Start PostgreSQL
```bash
service postgresql start
```

### Install Dependencies
```bash
pip install -r requirements.txt --break-system-packages
```

### Start the API Server
```bash
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

### Run Tests
```bash
python test_apis.py
```

### Access API
- Base URL: `http://localhost:8000`
- Docs: `http://localhost:8000/docs` (Swagger UI)
- ReDoc: `http://localhost:8000/redoc` (ReDoc)
- Health: `http://localhost:8000/health`

## Database Tables Created

All tables are automatically created on application startup via SQLAlchemy ORM:

1. `tenants` - Multi-tenancy support
2. `users` - User accounts
3. `receptionists` - Receptionist profiles
4. `visitors` - Guest/visitor records
5. `shops` - Restaurant/shop directory (NEW)
6. `shop_hours` - Operating hours per day (NEW)
7. `customers` - Customer profiles (NEW)
8. `reservations` - Reservation bookings (NEW)

## Indexes Created

For optimal query performance:
- `ix_shops_tenant_active` - Filter active shops per tenant
- `ix_shops_category` - Filter by category
- `ix_customers_email_tenant` - Unique email per tenant + lookup
- `ix_customers_tenant_active` - Filter active customers per tenant
- `ix_reservations_shop_date` - Find reservations by shop and date
- `ix_reservations_customer` - Find reservations by customer
- `ix_reservations_status` - Filter by reservation status
- `ix_reservations_tenant` - Tenant isolation
