#!/bin/bash

###############################################################################
# BARIYON Receptra - Phase 1-B Testing Script
# Tests all JWT + User Authentication & Tenant Management endpoints
###############################################################################

set -e

# Color codes
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Configuration
API_URL="http://localhost:8000"
TENANT_NAME="Test Company Phase 1B"
TENANT_SLUG="test-company-phase-1b"
USER_EMAIL="testuser@phase1b.com"
USER_PASSWORD="SecureTestPass123!"
USER_DISPLAY_NAME="Test User Phase 1B"

# Helper functions
print_header() {
    echo -e "\n${YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${YELLOW}$1${NC}"
    echo -e "${YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
}

print_success() {
    echo -e "${GREEN}✅ $1${NC}"
}

print_error() {
    echo -e "${RED}❌ $1${NC}"
}

check_api_running() {
    print_header "Checking if API is running..."

    if curl -s "$API_URL/health" > /dev/null 2>&1; then
        print_success "API is running"
    else
        print_error "API is not running at $API_URL"
        echo "Make sure Docker containers are up:"
        echo "  cd ~/www/receptra && docker compose up -d"
        exit 1
    fi
}

# Test 1: Health Check
test_health_check() {
    print_header "Test 1: Health Check"

    response=$(curl -s -X GET "$API_URL/health")

    if echo "$response" | jq -e '.status == "ok"' > /dev/null 2>&1; then
        print_success "Health check passed"
        echo "Response:"
        echo "$response" | jq .
    else
        print_error "Health check failed"
        echo "Response: $response"
        return 1
    fi
}

# Test 2: User Registration
test_registration() {
    print_header "Test 2: User Registration"

    response=$(curl -s -X POST "$API_URL/api/v1/auth/register" \
        -H "Content-Type: application/json" \
        -d "{
            \"tenant_name\": \"$TENANT_NAME\",
            \"tenant_slug\": \"$TENANT_SLUG\",
            \"email\": \"$USER_EMAIL\",
            \"password\": \"$USER_PASSWORD\",
            \"display_name\": \"$USER_DISPLAY_NAME\"
        }")

    # Check if response contains access_token
    if echo "$response" | jq -e '.access_token' > /dev/null 2>&1; then
        print_success "Registration successful"

        # Extract and save tokens
        ACCESS_TOKEN=$(echo "$response" | jq -r '.access_token')
        USER_ID=$(echo "$response" | jq -r '.user.id')
        TENANT_ID=$(echo "$response" | jq -r '.user.tenant_id')

        echo "Response:"
        echo "$response" | jq .

        # Save for later tests
        echo "$ACCESS_TOKEN" > /tmp/access_token.txt
        echo "$USER_ID" > /tmp/user_id.txt
        echo "$TENANT_ID" > /tmp/tenant_id.txt

        return 0
    else
        print_error "Registration failed"
        echo "Response: $response"
        return 1
    fi
}

# Test 3: User Login
test_login() {
    print_header "Test 3: User Login"

    response=$(curl -s -X POST "$API_URL/api/v1/auth/login" \
        -H "Content-Type: application/json" \
        -d "{
            \"email\": \"$USER_EMAIL\",
            \"password\": \"$USER_PASSWORD\",
            \"tenant_slug\": \"$TENANT_SLUG\"
        }")

    if echo "$response" | jq -e '.access_token' > /dev/null 2>&1; then
        print_success "Login successful"

        # Extract token
        LOGIN_TOKEN=$(echo "$response" | jq -r '.access_token')
        echo "$LOGIN_TOKEN" > /tmp/login_token.txt

        echo "Response:"
        echo "$response" | jq .

        return 0
    else
        print_error "Login failed"
        echo "Response: $response"
        return 1
    fi
}

# Test 4: Get Current User
test_get_current_user() {
    print_header "Test 4: Get Current User"

    if [ ! -f /tmp/access_token.txt ]; then
        print_error "No access token found. Skipping test."
        return 1
    fi

    ACCESS_TOKEN=$(cat /tmp/access_token.txt)

    response=$(curl -s -X GET "$API_URL/api/v1/auth/me" \
        -H "Authorization: Bearer $ACCESS_TOKEN")

    if echo "$response" | jq -e '.id' > /dev/null 2>&1; then
        print_success "Get current user successful"
        echo "Response:"
        echo "$response" | jq .
        return 0
    else
        print_error "Get current user failed"
        echo "Response: $response"
        return 1
    fi
}

# Test 5: Invalid Token Test
test_invalid_token() {
    print_header "Test 5: Invalid Token Rejection"

    response=$(curl -s -w "\n%{http_code}" -X GET "$API_URL/api/v1/auth/me" \
        -H "Authorization: Bearer invalid.token.here")

    http_code=$(echo "$response" | tail -n1)
    body=$(echo "$response" | head -n-1)

    if [ "$http_code" = "401" ]; then
        print_success "Invalid token correctly rejected with 401"
        echo "Response:"
        echo "$body" | jq .
        return 0
    else
        print_error "Invalid token should return 401, got $http_code"
        echo "Response: $body"
        return 1
    fi
}

# Test 6: Missing Token Test
test_missing_token() {
    print_header "Test 6: Missing Token Rejection"

    response=$(curl -s -w "\n%{http_code}" -X GET "$API_URL/api/v1/auth/me")

    http_code=$(echo "$response" | tail -n1)

    if [ "$http_code" = "403" ]; then
        print_success "Missing token correctly rejected with 403"
        return 0
    else
        print_error "Missing token should return 403, got $http_code"
        return 1
    fi
}

# Test 7: Wrong Password Test
test_wrong_password() {
    print_header "Test 7: Wrong Password Rejection"

    response=$(curl -s -w "\n%{http_code}" -X POST "$API_URL/api/v1/auth/login" \
        -H "Content-Type: application/json" \
        -d "{
            \"email\": \"$USER_EMAIL\",
            \"password\": \"WrongPassword123!\",
            \"tenant_slug\": \"$TENANT_SLUG\"
        }")

    http_code=$(echo "$response" | tail -n1)

    if [ "$http_code" = "401" ]; then
        print_success "Wrong password correctly rejected with 401"
        return 0
    else
        print_error "Wrong password should return 401, got $http_code"
        return 1
    fi
}

###############################################################################
# Main Test Execution
###############################################################################

main() {
    echo -e "\n${YELLOW}╔══════════════════════════════════════════════════════╗${NC}"
    echo -e "${YELLOW}║  BARIYON Receptra - Phase 1-B Testing Suite         ║${NC}"
    echo -e "${YELLOW}║  JWT + User Authentication & Tenant Management      ║${NC}"
    echo -e "${YELLOW}╚══════════════════════════════════════════════════════╝${NC}"

    # Track test results
    TESTS_PASSED=0
    TESTS_FAILED=0

    # Check API is running
    check_api_running

    # Run tests
    test_health_check && ((TESTS_PASSED++)) || ((TESTS_FAILED++))

    test_registration && ((TESTS_PASSED++)) || ((TESTS_FAILED++))

    test_login && ((TESTS_PASSED++)) || ((TESTS_FAILED++))

    test_get_current_user && ((TESTS_PASSED++)) || ((TESTS_FAILED++))

    test_invalid_token && ((TESTS_PASSED++)) || ((TESTS_FAILED++))

    test_missing_token && ((TESTS_PASSED++)) || ((TESTS_FAILED++))

    test_wrong_password && ((TESTS_PASSED++)) || ((TESTS_FAILED++))

    # Summary
    print_header "Test Summary"
    echo -e "${GREEN}Passed: $TESTS_PASSED${NC}"
    echo -e "${RED}Failed: $TESTS_FAILED${NC}"

    if [ $TESTS_FAILED -eq 0 ]; then
        echo -e "\n${GREEN}✅ All Phase 1-B tests passed!${NC}"
        echo -e "\n${YELLOW}Phase 1-B is complete and ready for deployment.${NC}"
        exit 0
    else
        echo -e "\n${RED}❌ Some tests failed. Check output above.${NC}"
        exit 1
    fi
}

# Run main function
main
