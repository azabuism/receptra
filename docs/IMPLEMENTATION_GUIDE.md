# BARIYON Receptra API - Implementation Guide

## Overview

This is a complete implementation of the BARIYON Receptra API, a multi-tenant receptionist management system built with FastAPI, SQLAlchemy, and PostgreSQL.

## Project Phases

### Phase 1-B: JWT Authentication ✅ COMPLETED
**Objective:** Implement user registration and login with JWT-based authentication

**Files:**
- `app/routers/auth.py` - Authentication endpoints
- `app/services/user.py` - User management service
- `app/models/user.py` - User and Tenant models
- `app/schemas/user.py` - User schemas

**Endpoints:**
- `POST /auth/register` - Register new user and create tenant
- `POST /auth/login` - Login and get JWT access token

**Features:**
- Bearer token authentication with HTTPBearer
- Argon2 password hashing
- JWT tokens with HS256 algorithm
- Tenant-based authentication
- Automatic database initialization

### Phase 2-A: User Management ✅ COMPLETED
**Objective:** Implement user profile management endpoints

**Files:**
- `app/routers/users.py` - User management endpoints
- `app/services/user.py` - User CRUD operations
- `app/schemas/user.py` - User request/response schemas

**Endpoints:**
- `GET /api/v1/users` - List all active users in tenant
- `GET /api/v1/users/{user_id}` - Get specific user
- `PUT /api/v1/users/{user_id}` - Update user profile (display_name, avatar_url, bio)
- `DELETE /api/v1/users/{user_id}` - Logical delete user

**Features:**
- Bearer token validation
- Tenant isolation (users can only see their own tenant's data)
- Permission checks (admin or user themselves can update)
- Logical deletion pattern (is_active flag)

### Phase 3-A: Receptionist Management ✅ COMPLETED
**Objective:** Implement receptionist management endpoints

**Files:**
- `app/models/receptionist.py` - Receptionist model
- `app/schemas/receptionist.py` - Receptionist schemas
- `app/services/receptionist.py` - Receptionist CRUD operations
- `app/routers/receptionists.py` - Receptionist endpoints
- Updated `app/models/user.py` - Added Tenant.receptionists relationship

**Endpoints:**
- `POST /api/v1/receptionists` - Create receptionist (admin only)
- `GET /api/v1/receptionists` - List all active receptionists
- `GET /api/v1/receptionists/{id}` - Get specific receptionist
- `PUT /api/v1/receptionists/{id}` - Update receptionist (admin only)
- `DELETE /api/v1/receptionists/{id}` - Delete receptionist (admin only)

**Features:**
- Receptionist attributes: name, email, phone, role, shift
- Admin-only create/update/delete operations
- Logical deletion pattern
- Cascade delete relationship with Tenant
- Email + tenant_id composite index for efficient lookups

### Phase 4-A: Visitor Management ✅ COMPLETED
**Objective:** Implement comprehensive visitor management with check-in/check-out tracking

**Files:**
- `app/models/visitor.py` - Visitor model
- `app/schemas/visitor.py` - Visitor schemas
- `app/services/visitor.py` - Visitor CRUD operations and check-in/check-out logic
- `app/routers/visitors.py` - Visitor endpoints
- Updated `app/models/user.py` - Added Tenant.visitors relationship

**Endpoints:**
- `POST /api/v1/visitors` - Create visitor
- `GET /api/v1/visitors` - List visitors (optional status filter)
- `GET /api/v1/visitors/{visitor_id}` - Get specific visitor
- `PUT /api/v1/visitors/{visitor_id}` - Update visitor
- `POST /api/v1/visitors/{visitor_id}/check-in` - Check in visitor
- `POST /api/v1/visitors/{visitor_id}/check-out` - Check out visitor
- `DELETE /api/v1/visitors/{visitor_id}` - Delete visitor

**Features:**
- Visitor attributes: name, email, phone, company, purpose, host_id
- Status tracking: pending, checked_in, checked_out
- Check-in/check-out timestamps
- Status-based filtering
- Tenant isolation
- Logical deletion pattern
- Composite indexes for common queries: (email, tenant_id) and (status, tenant_id)

## Architecture

### Database Models

**Tenants**
- Multi-tenant data isolation root
- Relationships to Users, Receptionists, Visitors
- Cascade delete to maintain referential integrity

**Users**
- Tenant-scoped user accounts
- Email unique per tenant
- Password hashing with Argon2
- Role-based access control (admin/user)
- Profile fields: display_name, avatar_url, bio

**Receptionists**
- Per-tenant receptionist records
- Shift scheduling support
- Email + tenant composite index

**Visitors**
- Guest tracking and management
- Linked to user (host) for assignment
- Status lifecycle tracking
- Check-in/out timestamp recording

### Authentication Flow

1. User registers with email, password, username, tenant_name, tenant_slug
2. System creates Tenant and User with admin role
3. User receives JWT access token
4. All subsequent requests include Bearer token
5. Token decoded to extract user_id, tenant_id, role
6. All queries filtered by tenant_id for isolation

### Dependency Injection

- `get_db()` - Provides AsyncSession for database operations
- `get_current_user()` - Validates JWT and returns current user
- Both used as FastAPI dependencies on endpoints

## Directory Structure

```
app/
├── __init__.py
├── main.py                      # FastAPI app entry point
├── database.py                  # Database configuration
├── models/
│   ├── __init__.py
│   ├── user.py                  # User, Tenant models
│   ├── receptionist.py          # Receptionist model
│   └── visitor.py               # Visitor model
├── schemas/
│   ├── __init__.py
│   ├── user.py                  # User/Tenant Pydantic schemas
│   ├── receptionist.py          # Receptionist Pydantic schemas
│   └── visitor.py               # Visitor Pydantic schemas
├── services/
│   ├── __init__.py
│   ├── user.py                  # User/Tenant business logic
│   ├── receptionist.py          # Receptionist business logic
│   └── visitor.py               # Visitor business logic
└── routers/
    ├── __init__.py
    ├── auth.py                  # Authentication endpoints
    ├── users.py                 # User management endpoints
    ├── receptionists.py         # Receptionist endpoints
    └── visitors.py              # Visitor endpoints
```

## Key Technical Decisions

### 1. Logical Deletion
All models use `is_active` boolean flag instead of hard deletes. This:
- Preserves audit trail
- Allows data recovery
- Maintains referential integrity
- Simplifies cascade operations

### 2. Multi-Tenant Architecture
Every significant table includes `tenant_id`:
- Ensures data isolation
- Simplifies permission checks
- Enables composite indexes
- Prevents cross-tenant data leaks

### 3. Async/Await Patterns
All database operations are async:
- Better performance under load
- Non-blocking I/O
- Scalable to thousands of concurrent users

### 4. SQLAlchemy 2.0 with asyncpg
- Modern SQL toolkit with async support
- Type hints and better IDE support
- Connection pooling for efficiency
- Prepared statements for security

### 5. Pydantic v2 Validation
- Strong request/response validation
- JSON schema generation
- Type safety
- Config via `from_attributes` for ORM model conversion

### 6. Separation of Concerns
- Models: Database schema definitions
- Services: Business logic and data access
- Schemas: Request/response validation
- Routers: HTTP endpoint definitions

## Testing

### Quick Start
```bash
# Build and run with Docker
docker-compose up --build

# Run test suite (in another terminal)
./test_phase_4a.sh
```

### Manual Testing
```bash
# Register
curl -X POST http://localhost:8000/auth/register \
  -H "Content-Type: application/json" \
  -d '{
    "email": "admin@example.com",
    "password": "password123",
    "username": "admin",
    "tenant_name": "Test Org",
    "tenant_slug": "test-org"
  }'

# List visitors with token
curl -X GET http://localhost:8000/api/v1/visitors \
  -H "Authorization: Bearer YOUR_TOKEN"
```

### Interactive Documentation
- Swagger UI: http://localhost:8000/docs
- ReDoc: http://localhost:8000/redoc

## Deployment Considerations

### Production Checklist
- [ ] Change JWT SECRET_KEY to strong random value
- [ ] Configure environment variables for database
- [ ] Set DEBUG=False
- [ ] Use HTTPS/TLS in reverse proxy
- [ ] Configure CORS appropriately (not `["*"]`)
- [ ] Set up database backups
- [ ] Configure logging and monitoring
- [ ] Use connection pooling
- [ ] Add rate limiting for auth endpoints
- [ ] Implement request validation for large payloads

### Scaling Opportunities
- Add Redis for JWT token blacklisting
- Implement request caching with Redis
- Use database read replicas
- Add API versioning for backward compatibility
- Implement pagination for list endpoints
- Add search/filtering capabilities
- Monitor slow queries and add indexes

## Future Enhancements

1. **Email Notifications**
   - Check-in/check-out alerts
   - Visitor arrival notifications

2. **Advanced Scheduling**
   - Receptionist schedule management
   - Availability checking

3. **Reporting & Analytics**
   - Visitor traffic patterns
   - Peak hour analysis
   - Dwell time metrics

4. **Mobile App**
   - Native mobile clients
   - Push notifications

5. **Integration APIs**
   - Badge printing system
   - Building access control
   - Email/calendar systems

## Troubleshooting

### Database Connection Issues
```bash
# Check PostgreSQL is running
docker-compose ps

# View logs
docker-compose logs postgres
```

### API Port Already in Use
```bash
# Find process on port 8000
lsof -i :8000

# Kill process if needed
kill -9 <PID>
```

### JWT Token Errors
- Verify token format: `Authorization: Bearer <token>`
- Check token expiration (30 minutes by default)
- Re-login to get new token

## Code Quality

All Python files compile without syntax errors and follow these patterns:

- Type hints on all function parameters and returns
- Async/await for all database operations
- Dependency injection for testability
- Logical separation of concerns
- Consistent error handling
- Composite indexes on frequently filtered columns

## Summary

This implementation provides a production-ready receptionist management system with:
- ✅ Complete multi-tenant architecture
- ✅ JWT authentication
- ✅ User management
- ✅ Receptionist management
- ✅ Comprehensive visitor tracking with check-in/check-out
- ✅ Docker containerization
- ✅ Full API documentation
- ✅ Test suite

Ready for deployment and testing!
