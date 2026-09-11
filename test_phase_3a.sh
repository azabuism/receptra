#!/bin/bash

###############################################################################
# BARIYON Receptra - Phase 3-A Testing Script
# Tests all Receptionist Management endpoints
###############################################################################

set -e

# Color codes
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Configuration
API_URL="http://localhost:8000"
USER_EMAIL="testuser3a@phase3a.com"
USER_PASSWORD="SecureTestPass123!"
USER_DISPLAY_NAME="Test Admin Phase 3A"

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

# First, register an admin user
test_registration() {
    print_header "Setup: User Registration (Admin)"

    response=$(curl -s -X POST "$API_URL/api/v1/auth/register" \
        -H "Content-Type: application/json" \
        -d "{
            \"email\": \"$USER_EMAIL\",
            \"password\": \"$USER_PASSWORD\",
            \"display_name\": \"$USER_DISPLAY_NAME\"
        }")

    if echo "$response" | jq -e '.access_token' > /dev/null 2>&1; then
        print_success "Registration successful"
        
        # Extract tokens
        ACCESS_TOKEN=$(echo "$response" | jq -r '.access_token')
        USER_ID=$(echo "$response" | jq -r '.user.id')
        
        echo "Access Token: ${ACCESS_TOKEN:0:20}..."
        echo "User ID: $USER_ID"
        
        # Save for later tests
        echo "$ACCESS_TOKEN" > /tmp/access_token_3a.txt
        echo "$USER_ID" > /tmp/user_id_3a.txt
        
        return 0
    else
        print_error "Registration failed"
        echo "Response: $response"
        return 1
    fi
}

# Test 1: Create Receptionist
test_create_receptionist() {
    print_header "Test 1: Create Receptionist (POST /api/v1/receptionists)"

    if [ ! -f /tmp/access_token_3a.txt ]; then
        print_error "No access token found. Skipping test."
        return 1
    fi

    ACCESS_TOKEN=$(cat /tmp/access_token_3a.txt)

    response=$(curl -s -X POST "$API_URL/api/v1/receptionists" \
        -H "Authorization: Bearer $ACCESS_TOKEN" \
        -H "Content-Type: application/json" \
        -d '{
            "name": "Tanaka Receptionist",
            "email": "tanaka@receptionist.com",
            "phone": "09012345678",
            "role": "receptionist",
            "shift": "morning"
        }')

    if echo "$response" | jq -e '.id' > /dev/null 2>&1; then
        print_success "Create receptionist successful"
        RECEPTIONIST_ID=$(echo "$response" | jq -r '.id')
        echo "$RECEPTIONIST_ID" > /tmp/receptionist_id_3a.txt
        echo "Response:"
        echo "$response" | jq .
        return 0
    else
        print_error "Create receptionist failed"
        echo "Response: $response"
        return 1
    fi
}

# Test 2: List Receptionists
test_list_receptionists() {
    print_header "Test 2: List Receptionists (GET /api/v1/receptionists)"

    if [ ! -f /tmp/access_token_3a.txt ]; then
        print_error "No access token found. Skipping test."
        return 1
    fi

    ACCESS_TOKEN=$(cat /tmp/access_token_3a.txt)

    response=$(curl -s -X GET "$API_URL/api/v1/receptionists" \
        -H "Authorization: Bearer $ACCESS_TOKEN")

    if echo "$response" | jq -e '.[0].id' > /dev/null 2>&1; then
        print_success "List receptionists successful"
        echo "Response (first receptionist):"
        echo "$response" | jq '.[0]'
        return 0
    else
        print_error "List receptionists failed"
        echo "Response: $response"
        return 1
    fi
}

# Test 3: Get Specific Receptionist
test_get_receptionist() {
    print_header "Test 3: Get Receptionist (GET /api/v1/receptionists/{id})"

    if [ ! -f /tmp/access_token_3a.txt ] || [ ! -f /tmp/receptionist_id_3a.txt ]; then
        print_error "Missing token or receptionist ID. Skipping test."
        return 1
    fi

    ACCESS_TOKEN=$(cat /tmp/access_token_3a.txt)
    RECEPTIONIST_ID=$(cat /tmp/receptionist_id_3a.txt)

    response=$(curl -s -X GET "$API_URL/api/v1/receptionists/$RECEPTIONIST_ID" \
        -H "Authorization: Bearer $ACCESS_TOKEN")

    if echo "$response" | jq -e '.id' > /dev/null 2>&1; then
        print_success "Get receptionist successful"
        echo "Response:"
        echo "$response" | jq .
        return 0
    else
        print_error "Get receptionist failed"
        echo "Response: $response"
        return 1
    fi
}

# Test 4: Update Receptionist
test_update_receptionist() {
    print_header "Test 4: Update Receptionist (PUT /api/v1/receptionists/{id})"

    if [ ! -f /tmp/access_token_3a.txt ] || [ ! -f /tmp/receptionist_id_3a.txt ]; then
        print_error "Missing token or receptionist ID. Skipping test."
        return 1
    fi

    ACCESS_TOKEN=$(cat /tmp/access_token_3a.txt)
    RECEPTIONIST_ID=$(cat /tmp/receptionist_id_3a.txt)

    response=$(curl -s -X PUT "$API_URL/api/v1/receptionists/$RECEPTIONIST_ID" \
        -H "Authorization: Bearer $ACCESS_TOKEN" \
        -H "Content-Type: application/json" \
        -d '{
            "shift": "afternoon",
            "role": "manager"
        }')

    if echo "$response" | jq -e '.id' > /dev/null 2>&1; then
        if [ "$(echo "$response" | jq -r '.shift')" == "afternoon" ]; then
            print_success "Update receptionist successful"
            echo "Response:"
            echo "$response" | jq .
            return 0
        fi
    fi

    print_error "Update receptionist failed"
    echo "Response: $response"
    return 1
}

# Test 5: Delete Receptionist
test_delete_receptionist() {
    print_header "Test 5: Delete Receptionist (DELETE /api/v1/receptionists/{id})"

    if [ ! -f /tmp/access_token_3a.txt ] || [ ! -f /tmp/receptionist_id_3a.txt ]; then
        print_error "Missing token or receptionist ID. Skipping test."
        return 1
    fi

    ACCESS_TOKEN=$(cat /tmp/access_token_3a.txt)
    RECEPTIONIST_ID=$(cat /tmp/receptionist_id_3a.txt)

    response=$(curl -s -w "\n%{http_code}" -X DELETE "$API_URL/api/v1/receptionists/$RECEPTIONIST_ID" \
        -H "Authorization: Bearer $ACCESS_TOKEN")

    http_code=$(echo "$response" | tail -n1)

    if [ "$http_code" = "204" ]; then
        print_success "Delete receptionist successful (204 No Content)"
        return 0
    else
        print_error "Delete receptionist failed with status $http_code"
        echo "Response: $response"
        return 1
    fi
}

###############################################################################
# Main Test Execution
###############################################################################

main() {
    echo -e "\n${YELLOW}╔══════════════════════════════════════════════════════╗${NC}"
    echo -e "${YELLOW}║  BARIYON Receptra - Phase 3-A Testing Suite         ║${NC}"
    echo -e "${YELLOW}║  Receptionist Management Endpoints                   ║${NC}"
    echo -e "${YELLOW}╚══════════════════════════════════════════════════════╝${NC}"

    # Track test results
    TESTS_PASSED=0
    TESTS_FAILED=0

    # Registration first
    test_registration && ((TESTS_PASSED++)) || ((TESTS_FAILED++))

    # Run receptionist management tests
    test_create_receptionist && ((TESTS_PASSED++)) || ((TESTS_FAILED++))

    test_list_receptionists && ((TESTS_PASSED++)) || ((TESTS_FAILED++))

    test_get_receptionist && ((TESTS_PASSED++)) || ((TESTS_FAILED++))

    test_update_receptionist && ((TESTS_PASSED++)) || ((TESTS_FAILED++))

    test_delete_receptionist && ((TESTS_PASSED++)) || ((TESTS_FAILED++))

    # Summary
    print_header "Test Summary"
    echo -e "${GREEN}Passed: $TESTS_PASSED${NC}"
    echo -e "${RED}Failed: $TESTS_FAILED${NC}"

    if [ $TESTS_FAILED -eq 0 ]; then
        echo -e "\n${GREEN}✅ All Phase 3-A tests passed!${NC}"
        echo -e "\n${YELLOW}Phase 3-A is complete and ready for deployment.${NC}"
        exit 0
    else
        echo -e "\n${RED}❌ Some tests failed. Check output above.${NC}"
        exit 1
    fi
}

# Run main function
main
