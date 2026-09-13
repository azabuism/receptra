# Receptra API Quick Reference

## Base URL
```
http://localhost:8000
```

## Authentication
All endpoints require a Bearer token in the Authorization header:
```
Authorization: Bearer {JWT_TOKEN}
```

Get a token via:
```
POST /auth/register or /auth/login
```

---

## Shop Endpoints

### Register Shop
```
POST /api/v1/shops/register
Content-Type: application/json

{
  "name": "Pizza Palace",
  "description": "Authentic Italian pizza",
  "category": "Italian",
  "address": "123 Main St",
  "latitude": 40.7128,
  "longitude": -74.0060,
  "phone": "555-1234",
  "email": "info@pizzapalace.com",
  "website": "https://pizzapalace.com",
  "hours": [
    {
      "day_of_week": 0,
      "opening_time": "11:00",
      "closing_time": "23:00",
      "is_closed": false
    }
  ]
}

Response: 201 Created
{
  "id": "abc123...",
  "name": "Pizza Palace",
  "category": "Italian",
  ...
}
```

### Search Shops
```
GET /api/v1/shops/search?keyword=pizza&latitude=40.7128&longitude=-74.0060&radius_km=10&sort_by=rating&page=1&limit=10

Query Parameters:
  keyword (optional): Search term
  category (optional): Filter by category
  latitude (optional): GPS latitude for distance filter
  longitude (optional): GPS longitude for distance filter
  radius_km (optional, default=10): Search radius in kilometers
  sort_by (optional, default=rating): rating|distance|popularity|newest
  page (optional, default=1): Page number (1-indexed)
  limit (optional, default=10): Results per page (1-100)

Response: 200 OK
{
  "total": 42,
  "page": 1,
  "limit": 10,
  "items": [
    {
      "id": "shop123...",
      "name": "Pizza Palace",
      "category": "Italian",
      "rating": 4.8,
      ...
    }
  ]
}
```

### Get Shop Details
```
GET /api/v1/shops/{shop_id}

Response: 200 OK
{
  "id": "shop123...",
  "name": "Pizza Palace",
  "hours": [
    {
      "id": "hours123...",
      "day_of_week": 0,
      "opening_time": "11:00",
      "closing_time": "23:00",
      "is_closed": false
    }
  ],
  ...
}
```

---

## Customer Endpoints

### Register Customer
```
POST /api/v1/customers/register
Content-Type: application/json

{
  "email": "john@example.com",
  "phone": "555-5678",
  "display_name": "John Doe",
  "avatar_url": "https://example.com/avatar.jpg",
  "address": "456 Elm St",
  "latitude": 40.7580,
  "longitude": -73.9855
}

Response: 201 Created
{
  "id": "customer123...",
  "email": "john@example.com",
  "created_at": "2026-09-09T12:00:00"
}
```

### Get Customer Profile
```
GET /api/v1/customers/{customer_id}

Response: 200 OK
{
  "id": "customer123...",
  "email": "john@example.com",
  "phone": "555-5678",
  "display_name": "John Doe",
  "vip_level": "regular",
  "lifetime_value": 150.50,
  "reservation_count": 3,
  ...
}
```

### Search Customer by Email
```
GET /api/v1/customers/search/by-email?email=john@example.com

Response: 200 OK
{
  "id": "customer123...",
  "email": "john@example.com",
  ...
}
```

### Update Customer Profile
```
PUT /api/v1/customers/{customer_id}
Content-Type: application/json

{
  "phone": "555-9999",
  "display_name": "John Smith",
  "subscription_preferences": "weekly_newsletter"
}

Response: 200 OK
{
  "id": "customer123...",
  "email": "john@example.com",
  "phone": "555-9999",
  "display_name": "John Smith",
  ...
}
```

---

## Reservation Endpoints

### Create Reservation
```
POST /api/v1/reservations/create
Content-Type: application/json

{
  "shop_id": "shop123...",
  "customer_id": "customer123...",
  "reservation_date": "2026-09-10T19:30:00",
  "party_size": 4,
  "special_requests": "Window seat, no peanuts"
}

Response: 201 Created
{
  "id": "reservation123...",
  "shop_id": "shop123...",
  "customer_id": "customer123...",
  "reservation_date": "2026-09-10T19:30:00",
  "party_size": 4,
  "status": "PENDING",
  "created_at": "2026-09-09T12:00:00"
}

Errors:
  400: Reservation date must be in future
  404: Shop or customer not found
```

### Get Reservation Details
```
GET /api/v1/reservations/{reservation_id}

Response: 200 OK
{
  "id": "reservation123...",
  "shop_id": "shop123...",
  "customer_id": "customer123...",
  "party_size": 4,
  "status": "PENDING",
  "special_requests": "Window seat, no peanuts",
  "created_at": "2026-09-09T12:00:00"
}
```

### List Shop Reservations
```
GET /api/v1/reservations/shop/{shop_id}?status_filter=PENDING&page=1&limit=10

Query Parameters:
  status_filter (optional): PENDING|CONFIRMED|CANCELLED|NO_SHOW|COMPLETED
  page (optional, default=1): Page number
  limit (optional, default=10): Results per page

Response: 200 OK
{
  "total": 25,
  "page": 1,
  "limit": 10,
  "items": [
    {
      "id": "reservation123...",
      "status": "PENDING",
      ...
    }
  ]
}
```

### Update Reservation
```
PUT /api/v1/reservations/{reservation_id}
Content-Type: application/json

{
  "status": "CONFIRMED",
  "party_size": 5,
  "arrival_time": "2026-09-10T19:45:00"
}

Response: 200 OK
{
  "id": "reservation123...",
  "status": "CONFIRMED",
  "party_size": 5,
  "arrival_time": "2026-09-10T19:45:00",
  ...
}
```

### Cancel Reservation
```
DELETE /api/v1/reservations/{reservation_id}?cancellation_reason=guest_requested

Query Parameters:
  cancellation_reason (optional): Reason for cancellation

Response: 204 No Content
```

---

## Status Codes

| Code | Meaning |
|------|---------|
| 200 | OK - Request successful |
| 201 | Created - Resource created |
| 204 | No Content - Successful deletion |
| 400 | Bad Request - Invalid input |
| 401 | Unauthorized - Invalid token |
| 404 | Not Found - Resource doesn't exist |
| 409 | Conflict - Email already registered |
| 500 | Server Error - Internal error |

---

## Error Response Format

```json
{
  "detail": "Error message describing what went wrong"
}
```

---

## Rate Limits
Currently unlimited (configure in production)

## CORS
Currently allows all origins (configure in production)

## Database
PostgreSQL on localhost:5432
- Database: receptra_db
- User: receptra_user

---

## Example Usage Flow

### 1. Register and Login
```bash
# Register
curl -X POST http://localhost:8000/auth/register \
  -H "Content-Type: application/json" \
  -d '{
    "email": "owner@example.com",
    "password": "password123",
    "username": "owner",
    "tenant_name": "My Restaurant Group",
    "tenant_slug": "my-restaurant-group"
  }'

# Response includes access_token
TOKEN="eyJhbGciOiJIUzI1NiIs..."
```

### 2. Register a Shop
```bash
curl -X POST http://localhost:8000/api/v1/shops/register \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "My Restaurant",
    "category": "Italian",
    "address": "123 Main St"
  }'
```

### 3. Register a Customer
```bash
curl -X POST http://localhost:8000/api/v1/customers/register \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "email": "customer@example.com",
    "phone": "555-1234"
  }'
```

### 4. Create a Reservation
```bash
curl -X POST http://localhost:8000/api/v1/reservations/create \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "shop_id": "SHOP_ID",
    "customer_id": "CUSTOMER_ID",
    "reservation_date": "2026-09-10T19:30:00",
    "party_size": 4
  }'
```

### 5. Search Available Shops
```bash
curl -X GET 'http://localhost:8000/api/v1/shops/search?keyword=italian&latitude=40.7128&longitude=-74.0060&radius_km=5' \
  -H "Authorization: Bearer $TOKEN"
```
