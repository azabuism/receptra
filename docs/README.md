# BARIYON Receptra API

Multi-tenant receptionist management API built with FastAPI, SQLAlchemy, and PostgreSQL.

## Project Structure

```
app/
├── models/           # SQLAlchemy ORM models (User, Tenant, Receptionist, Visitor)
├── schemas/          # Pydantic schemas for request/response validation
├── services/         # Business logic layer for database operations
├── routers/          # FastAPI route handlers
├── database.py       # Database configuration and session management
└── main.py           # FastAPI application entry point
```

## Features

### Phase 1-B: JWT Authentication
- User registration and login
- JWT-based authentication with HS256
- Bearer token validation
- Argon2 password hashing

### Phase 2-A: User Management
- List all active users in a tenant
- Get user by ID
- Update user profile (display name, avatar, bio)
- Delete user (logical delete)

### Phase 3-A: Receptionist Management
- Create receptionist (admin only)
- List all active receptionists
- Get receptionist by ID
- Update receptionist (admin only)
- Delete receptionist (admin only)

### Phase 4-A: Visitor Management
- Create visitor
- List all active visitors (with optional status filter)
- Get visitor by ID
- Update visitor
- Check in visitor (sets status to "checked_in", records check_in_at)
- Check out visitor (sets status to "checked_out", records check_out_at)
- Delete visitor (logical delete)

## Setup and Installation

### Prerequisites
- Docker and Docker Compose
- Python 3.11+
- PostgreSQL 16 (or use Docker)

### Using Docker (Recommended)

1. **Build and start the containers:**
   ```bash
   docker-compose up --build
   ```

2. **The API will be available at:** `http://localhost:8000`

3. **API Documentation:** `http://localhost:8000/docs`

### Manual Setup (Without Docker)

1. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

2. **Set up PostgreSQL database:**
   ```bash
   createdb receptra_db
   createuser receptra_user
   psql receptra_db << EOF
   ALTER USER receptra_user WITH PASSWORD 'receptra_password';
   GRANT ALL PRIVILEGES ON DATABASE receptra_db TO receptra_user;
   EOF
   ```

3. **Run the application:**
   ```bash
   uvicorn app.main:app --reload
   ```

## API Endpoints

### Authentication
- `POST /auth/register` - Register new user and create tenant
- `POST /auth/login` - Login and get access token

### Users (Phase 2-A)
- `GET /api/v1/users` - List all users
- `GET /api/v1/users/{user_id}` - Get user
- `PUT /api/v1/users/{user_id}` - Update user
- `DELETE /api/v1/users/{user_id}` - Delete user

### Receptionists (Phase 3-A)
- `POST /api/v1/receptionists` - Create receptionist
- `GET /api/v1/receptionists` - List receptionists
- `GET /api/v1/receptionists/{id}` - Get receptionist
- `PUT /api/v1/receptionists/{id}` - Update receptionist
- `DELETE /api/v1/receptionists/{id}` - Delete receptionist

### Visitors (Phase 4-A)
- `POST /api/v1/visitors` - Create visitor
- `GET /api/v1/visitors` - List visitors (optionally filter by status)
- `GET /api/v1/visitors/{visitor_id}` - Get visitor
- `PUT /api/v1/visitors/{visitor_id}` - Update visitor
- `POST /api/v1/visitors/{visitor_id}/check-in` - Check in visitor
- `POST /api/v1/visitors/{visitor_id}/check-out` - Check out visitor
- `DELETE /api/v1/visitors/{visitor_id}` - Delete visitor

## Testing

### Run Test Script (Phase 4-A)

After the API is running:

```bash
./test_phase_4a.sh
```

This script will:
1. Register a new admin user and tenant
2. Create a receptionist
3. Create multiple visitors
4. Test check-in/check-out functionality
5. Test visitor filtering by status
6. Test deletion

### Manual API Testing

Using curl:

```bash
# Register
curl -X POST http://localhost:8000/auth/register \
  -H "Content-Type: application/json" \
  -d '{
    "email": "admin@example.com",
    "password": "password123",
    "username": "admin",
    "tenant_name": "My Company",
    "tenant_slug": "my-company"
  }'

# Login
curl -X POST http://localhost:8000/auth/login \
  -H "Content-Type: application/json" \
  -d '{
    "email": "admin@example.com",
    "password": "password123",
    "tenant_slug": "my-company"
  }'

# List visitors
curl -X GET http://localhost:8000/api/v1/visitors \
  -H "Authorization: Bearer YOUR_ACCESS_TOKEN"
```

### Using Swagger UI

Visit `http://localhost:8000/docs` for interactive API documentation.

## Database Schema

### Users
- id (UUID)
- tenant_id (Foreign Key → Tenants)
- username, email, password_hash
- display_name, avatar_url, bio
- role (user/admin)
- is_active (logical delete flag)
- created_at, updated_at

### Receptionists
- id (UUID)
- tenant_id (Foreign Key → Tenants)
- name, email, phone
- role, shift
- is_active
- created_at, updated_at

### Visitors
- id (UUID)
- tenant_id (Foreign Key → Tenants)
- name, email, phone, company
- purpose
- host_id (Foreign Key → Users)
- status (pending/checked_in/checked_out)
- check_in_at, check_out_at
- is_active
- created_at, updated_at

### Tenants
- id (UUID)
- name, slug (unique)
- created_at

## Security Features

- **JWT Authentication:** Secure token-based authentication
- **Bearer Token Validation:** HTTPBearer security scheme
- **Password Hashing:** Argon2 password hashing
- **Tenant Isolation:** All data filtered by tenant_id
- **Permission Checks:** Admin-only endpoints with role validation
- **CORS:** Configured for cross-origin requests

## Development

### Run with auto-reload:
```bash
uvicorn app.main:app --reload
```

### Run tests:
```bash
./test_phase_4a.sh
```

### Database migrations (if needed):
The application automatically creates tables on startup via `Base.metadata.create_all()`.

## Environment Variables

```bash
DATABASE_URL=postgresql+asyncpg://receptra_user:receptra_password@localhost:5432/receptra_db
```

## License

MIT

## Implementation Notes

- Logical deletion is used throughout (is_active flag) instead of hard deletes
- SQLAlchemy relationships use cascade delete-orphan for referential integrity
- All timestamps use UTC
- Multi-tenant architecture with tenant_id-based isolation
- Composite indexes for common query patterns
