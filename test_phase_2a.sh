#!/bin/bash

###############################################################################
# BARIYON Receptra - Phase 2-A Testing Script
# Tests all User Management endpoints
###############################################################################

set -e

# Color codes
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Configuration
API_URL="http://localhost:8000"
USER_EMAIL="testuser2a@phase2a.com"
USER_PASSWORD="SecureTestPass123!"
USER_DISPLAY_NAME="Test User Phase 2A"

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

# First, register a user
test_registration() {
    print_header "Setup: User Registration"

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
        echo "$ACCESS_TOKEN" > /tmp/access_token_2a.txt
        echo "$USER_ID" > /tmp/user_id_2a.txt
        
        return 0
    else
        print_error "Registration failed"
        echo "Response: $response"
        return 1
    fi
}

# Test 1: List Users
test_list_users() {
    print_header "Test 1: List Users (GET /api/v1/users)"

    if [ ! -f /tmp/access_token_2a.txt ]; then
        print_error "No access token found. Skipping test."
        return 1
    fi

    ACCESS_TOKEN=$(cat /tmp/access_token_2a.txt)

    response=$(curl -s -X GET "$API_URL/api/v1/users" \
        -H "Authorization: Bearer $ACCESS_TOKEN")

    if echo "$response" | jq -e '.[0].id' > /dev/null 2>&1; then
        print_success "List users successful"
        echo "Response (first user):"
        echo "$response" | jq '.[0]'
        return 0
    else
        print_error "List users failed"
        echo "Response: $response"
        return 1
    fi
}

# Test 2: Get Specific User
test_get_user() {
    print_header "Test 2: Get User (GET /api/v1/users/{user_id})"

    if [ ! -f /tmp/access_token_2a.txt ] || [ ! -f /tmp/user_id_2a.txt ]; then
        print_error "Missing token or user ID. Skipping test."
        return 1
    fi

    ACCESS_TOKEN=$(cat /tmp/access_token_2a.txt)
    USER_ID=$(cat /tmp/user_id_2a.txt)

    response=$(curl -s -X GET "$API_URL/api/v1/users/$USER_ID" \
        -H "Authorization: Bearer $ACCESS_TOKEN")

    if echo "$response" | jq -e '.id' > /dev/null 2>&1; then
        print_success "Get user successful"
        echo "Response:"
        echo "$response" | jq .
        return 0
    else
        print_error "Get user failed"
        echo "Response: $response"
        return 1
    fi
}

# Test 3: Update User Profile
test_update_user() {
    print_header "Test 3: Update User (PUT /api/v1/users/{user_id})"

    if [ ! -f /tmp/access_token_2a.txt ] || [ ! -f /tmp/user_id_2a.txt ]; then
        print_error "Missing token or user ID. Skipping test."
        return 1
    fi

    ACCESS_TOKEN=$(cat /tmp/access_token_2a.txt)
    USER_ID=$(cat /tmp/user_id_2a.txt)

    response=$(curl -s -X PUT "$API_URL/api/v1/users/$USER_ID" \
        -H "Authorization: Bearer $ACCESS_TOKEN" \
        -H "Content-Type: application/json" \
        -d "{
            \"display_name\": \"Updated User Name\",
            \"bio\": \"This is my bio\",
            \"avatar_url\": \"https://example.com/avatar.jpg\"
        }")

    if echo "$response" | jq -e '.display_name' > /dev/null 2>&1; then
        if [ "$(echo "$response" | jq -r '.display_name')" == "Updated User Name" ]; then
            print_success "Update user successful"
            echo "Response:"
            echo "$response" | jq .
            return 0
        fi
    fi

    print_error "Update user failed"
    echo "Response: $response"
    return 1
}

# Test 4: Delete User (Logical)
test_delete_user() {
    print_header "Test 4: Delete User (DELETE /api/v1/users/{user_id})"

    if [ ! -f /tmp/access_token_2a.txt ] || [ ! -f /tmp/user_id_2a.txt ]; then
        print_error "Missing token or user ID. Skipping test."
        return 1
    fi

    ACCESS_TOKEN=$(cat /tmp/access_token_2a.txt)
    USER_ID=$(cat /tmp/user_id_2a.txt)

    response=$(curl -s -w "\n%{http_code}" -X DELETE "$API_URL/api/v1/users/$USER_ID" \
        -H "Authorization: Bearer $ACCESS_TOKEN")

    http_code=$(echo "$response" | tail -n1)

    if [ "$http_code" = "204" ]; then
        print_success "Delete user successful (204 No Content)"
        return 0
    else
        print_error "Delete user failed with status $http_code"
        echo "Response: $response"
        return 1
    fi
}

###############################################################################
# Main Test Execution
###############################################################################

main() {
    echo -e "\n${YELLOW}╔══════════════════════════════════════════════════════╗${NC}"
    echo -e "${YELLOW}║  BARIYON Receptra - Phase 2-A Testing Suite         ║${NC}"
    echo -e "${YELLOW}║  User Management Endpoints                           ║${NC}"
    echo -e "${YELLOW}╚══════════════════════════════════════════════════════╝${NC}"

    # Track test results
    TESTS_PASSED=0
    TESTS_FAILED=0

    # Registration first
    test_registration && ((TESTS_PASSED++)) || ((TESTS_FAILED++))

    # Run user management tests
    test_list_users && ((TESTS_PASSED++)) || ((TESTS_FAILED++))

    test_get_user && ((TESTS_PASSED++)) || ((TESTS_FAILED++))

    test_update_user && ((TESTS_PASSED++)) || ((TESTS_FAILED++))

    test_delete_user && ((TESTS_PASSED++)) || ((TESTS_FAILED++))

    # Summary
    print_header "Test Summary"
    echo -e "${GREEN}Passed: $TESTS_PASSED${NC}"
    echo -e "${RED}Failed: $TESTS_FAILED${NC}"

    if [ $TESTS_FAILED -eq 0 ]; then
        echo -e "\n${GREEN}✅ All Phase 2-A tests passed!${NC}"
        echo -e "\n${YELLOW}Phase 2-A is complete and ready for deployment.${NC}"
        exit 0
    else
        echo -e "\n${RED}❌ Some tests failed. Check output above.${NC}"
        exit 1
    fi
}

# Run main function
main
