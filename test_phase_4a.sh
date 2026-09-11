#!/bin/bash

###############################################################################
# BARIYON Receptra - Phase 4-A Testing Script
# Tests all Visitor Management endpoints
###############################################################################

set -e

# Color codes
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Configuration
API_URL="http://localhost:8000"
USER_EMAIL="testuser4a@phase4a.com"
USER_PASSWORD="SecureTestPass123!"
USER_DISPLAY_NAME="Test Admin Phase 4A"

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
        echo "$ACCESS_TOKEN" > /tmp/access_token_4a.txt
        echo "$USER_ID" > /tmp/user_id_4a.txt
        
        return 0
    else
        print_error "Registration failed"
        echo "Response: $response"
        return 1
    fi
}

# Test 1: Create Visitor
test_create_visitor() {
    print_header "Test 1: Create Visitor (POST /api/v1/visitors)"

    if [ ! -f /tmp/access_token_4a.txt ] || [ ! -f /tmp/user_id_4a.txt ]; then
        print_error "No access token or user ID found. Skipping test."
        return 1
    fi

    ACCESS_TOKEN=$(cat /tmp/access_token_4a.txt)
    USER_ID=$(cat /tmp/user_id_4a.txt)

    response=$(curl -s -X POST "$API_URL/api/v1/visitors" \
        -H "Authorization: Bearer $ACCESS_TOKEN" \
        -H "Content-Type: application/json" \
        -d "{
            \"name\": \"Yamada Taro\",
            \"email\": \"yamada@example.com\",
            \"phone\": \"09012345678\",
            \"company\": \"Test Company Inc\",
            \"purpose\": \"Business Meeting\",
            \"host_id\": \"$USER_ID\"
        }")

    if echo "$response" | jq -e '.id' > /dev/null 2>&1; then
        print_success "Create visitor successful"
        VISITOR_ID=$(echo "$response" | jq -r '.id')
        echo "$VISITOR_ID" > /tmp/visitor_id_4a.txt
        echo "Response:"
        echo "$response" | jq .
        return 0
    else
        print_error "Create visitor failed"
        echo "Response: $response"
        return 1
    fi
}

# Test 2: List Visitors
test_list_visitors() {
    print_header "Test 2: List Visitors (GET /api/v1/visitors)"

    if [ ! -f /tmp/access_token_4a.txt ]; then
        print_error "No access token found. Skipping test."
        return 1
    fi

    ACCESS_TOKEN=$(cat /tmp/access_token_4a.txt)

    response=$(curl -s -X GET "$API_URL/api/v1/visitors" \
        -H "Authorization: Bearer $ACCESS_TOKEN")

    if echo "$response" | jq -e '.[0].id' > /dev/null 2>&1; then
        print_success "List visitors successful"
        echo "Total visitors: $(echo "$response" | jq 'length')"
        echo "First visitor:"
        echo "$response" | jq '.[0]'
        return 0
    else
        print_error "List visitors failed"
        echo "Response: $response"
        return 1
    fi
}

# Test 3: Get Specific Visitor
test_get_visitor() {
    print_header "Test 3: Get Visitor (GET /api/v1/visitors/{id})"

    if [ ! -f /tmp/access_token_4a.txt ] || [ ! -f /tmp/visitor_id_4a.txt ]; then
        print_error "Missing token or visitor ID. Skipping test."
        return 1
    fi

    ACCESS_TOKEN=$(cat /tmp/access_token_4a.txt)
    VISITOR_ID=$(cat /tmp/visitor_id_4a.txt)

    response=$(curl -s -X GET "$API_URL/api/v1/visitors/$VISITOR_ID" \
        -H "Authorization: Bearer $ACCESS_TOKEN")

    if echo "$response" | jq -e '.id' > /dev/null 2>&1; then
        print_success "Get visitor successful"
        echo "Response:"
        echo "$response" | jq .
        return 0
    else
        print_error "Get visitor failed"
        echo "Response: $response"
        return 1
    fi
}

# Test 4: Update Visitor
test_update_visitor() {
    print_header "Test 4: Update Visitor (PUT /api/v1/visitors/{id})"

    if [ ! -f /tmp/access_token_4a.txt ] || [ ! -f /tmp/visitor_id_4a.txt ]; then
        print_error "Missing token or visitor ID. Skipping test."
        return 1
    fi

    ACCESS_TOKEN=$(cat /tmp/access_token_4a.txt)
    VISITOR_ID=$(cat /tmp/visitor_id_4a.txt)

    response=$(curl -s -X PUT "$API_URL/api/v1/visitors/$VISITOR_ID" \
        -H "Authorization: Bearer $ACCESS_TOKEN" \
        -H "Content-Type: application/json" \
        -d '{
            "purpose": "Updated Business Meeting",
            "company": "Updated Company Ltd"
        }')

    if echo "$response" | jq -e '.id' > /dev/null 2>&1; then
        if [ "$(echo "$response" | jq -r '.purpose')" == "Updated Business Meeting" ]; then
            print_success "Update visitor successful"
            echo "Response:"
            echo "$response" | jq .
            return 0
        fi
    fi

    print_error "Update visitor failed"
    echo "Response: $response"
    return 1
}

# Test 5: Check-in Visitor
test_check_in_visitor() {
    print_header "Test 5: Check-in Visitor (POST /api/v1/visitors/{id}/check-in)"

    if [ ! -f /tmp/access_token_4a.txt ] || [ ! -f /tmp/visitor_id_4a.txt ]; then
        print_error "Missing token or visitor ID. Skipping test."
        return 1
    fi

    ACCESS_TOKEN=$(cat /tmp/access_token_4a.txt)
    VISITOR_ID=$(cat /tmp/visitor_id_4a.txt)

    response=$(curl -s -X POST "$API_URL/api/v1/visitors/$VISITOR_ID/check-in" \
        -H "Authorization: Bearer $ACCESS_TOKEN" \
        -H "Content-Type: application/json")

    if echo "$response" | jq -e '.id' > /dev/null 2>&1; then
        status=$(echo "$response" | jq -r '.status')
        check_in_at=$(echo "$response" | jq -r '.check_in_at')
        
        if [ "$status" == "checked_in" ] && [ "$check_in_at" != "null" ]; then
            print_success "Check-in visitor successful"
            echo "Status: $status"
            echo "Check-in time: $check_in_at"
            echo "Response:"
            echo "$response" | jq .
            return 0
        fi
    fi

    print_error "Check-in visitor failed"
    echo "Response: $response"
    return 1
}

# Test 6: List Visitors with Status Filter (pending)
test_list_visitors_pending() {
    print_header "Test 6a: List Pending Visitors (GET /api/v1/visitors?status=pending)"

    if [ ! -f /tmp/access_token_4a.txt ]; then
        print_error "No access token found. Skipping test."
        return 1
    fi

    ACCESS_TOKEN=$(cat /tmp/access_token_4a.txt)

    # Create another visitor in pending status
    USER_ID=$(cat /tmp/user_id_4a.txt)
    create_response=$(curl -s -X POST "$API_URL/api/v1/visitors" \
        -H "Authorization: Bearer $ACCESS_TOKEN" \
        -H "Content-Type: application/json" \
        -d "{
            \"name\": \"Sato Hanako\",
            \"email\": \"sato@example.com\",
            \"phone\": \"09087654321\",
            \"company\": \"Another Company\",
            \"purpose\": \"Site Visit\",
            \"host_id\": \"$USER_ID\"
        }")

    # Now list pending visitors
    response=$(curl -s -X GET "$API_URL/api/v1/visitors?status=pending" \
        -H "Authorization: Bearer $ACCESS_TOKEN")

    if echo "$response" | jq -e '.[0].status' > /dev/null 2>&1; then
        all_pending=true
        for row in $(echo "$response" | jq -r '.[] | @base64'); do
            status=$(echo $row | base64 -d | jq -r '.status')
            if [ "$status" != "pending" ]; then
                all_pending=false
                break
            fi
        done

        if [ "$all_pending" = true ]; then
            print_success "List pending visitors successful"
            echo "Pending visitors count: $(echo "$response" | jq 'length')"
            return 0
        fi
    fi

    print_error "List pending visitors failed or filter not working"
    echo "Response: $response"
    return 1
}

# Test 6b: List Visitors with Status Filter (checked_in)
test_list_visitors_checked_in() {
    print_header "Test 6b: List Checked-in Visitors (GET /api/v1/visitors?status=checked_in)"

    if [ ! -f /tmp/access_token_4a.txt ]; then
        print_error "No access token found. Skipping test."
        return 1
    fi

    ACCESS_TOKEN=$(cat /tmp/access_token_4a.txt)

    response=$(curl -s -X GET "$API_URL/api/v1/visitors?status=checked_in" \
        -H "Authorization: Bearer $ACCESS_TOKEN")

    if echo "$response" | jq -e '.[0].status' > /dev/null 2>&1; then
        all_checked_in=true
        for row in $(echo "$response" | jq -r '.[] | @base64'); do
            status=$(echo $row | base64 -d | jq -r '.status')
            if [ "$status" != "checked_in" ]; then
                all_checked_in=false
                break
            fi
        done

        if [ "$all_checked_in" = true ]; then
            print_success "List checked-in visitors successful"
            echo "Checked-in visitors count: $(echo "$response" | jq 'length')"
            return 0
        fi
    fi

    print_error "List checked-in visitors failed or filter not working"
    echo "Response: $response"
    return 1
}

# Test 7: Check-out Visitor
test_check_out_visitor() {
    print_header "Test 7: Check-out Visitor (POST /api/v1/visitors/{id}/check-out)"

    if [ ! -f /tmp/access_token_4a.txt ] || [ ! -f /tmp/visitor_id_4a.txt ]; then
        print_error "Missing token or visitor ID. Skipping test."
        return 1
    fi

    ACCESS_TOKEN=$(cat /tmp/access_token_4a.txt)
    VISITOR_ID=$(cat /tmp/visitor_id_4a.txt)

    response=$(curl -s -X POST "$API_URL/api/v1/visitors/$VISITOR_ID/check-out" \
        -H "Authorization: Bearer $ACCESS_TOKEN" \
        -H "Content-Type: application/json")

    if echo "$response" | jq -e '.id' > /dev/null 2>&1; then
        status=$(echo "$response" | jq -r '.status')
        check_out_at=$(echo "$response" | jq -r '.check_out_at')
        
        if [ "$status" == "checked_out" ] && [ "$check_out_at" != "null" ]; then
            print_success "Check-out visitor successful"
            echo "Status: $status"
            echo "Check-out time: $check_out_at"
            echo "Response:"
            echo "$response" | jq .
            return 0
        fi
    fi

    print_error "Check-out visitor failed"
    echo "Response: $response"
    return 1
}

# Test 8: Delete Visitor
test_delete_visitor() {
    print_header "Test 8: Delete Visitor (DELETE /api/v1/visitors/{id})"

    if [ ! -f /tmp/access_token_4a.txt ] || [ ! -f /tmp/visitor_id_4a.txt ]; then
        print_error "Missing token or visitor ID. Skipping test."
        return 1
    fi

    ACCESS_TOKEN=$(cat /tmp/access_token_4a.txt)
    VISITOR_ID=$(cat /tmp/visitor_id_4a.txt)

    response=$(curl -s -w "\n%{http_code}" -X DELETE "$API_URL/api/v1/visitors/$VISITOR_ID" \
        -H "Authorization: Bearer $ACCESS_TOKEN")

    http_code=$(echo "$response" | tail -n1)

    if [ "$http_code" = "204" ]; then
        print_success "Delete visitor successful (204 No Content)"
        return 0
    else
        print_error "Delete visitor failed with status $http_code"
        echo "Response: $response"
        return 1
    fi
}

###############################################################################
# Main Test Execution
###############################################################################

main() {
    echo -e "\n${YELLOW}╔══════════════════════════════════════════════════════╗${NC}"
    echo -e "${YELLOW}║  BARIYON Receptra - Phase 4-A Testing Suite         ║${NC}"
    echo -e "${YELLOW}║  Visitor Management Endpoints                        ║${NC}"
    echo -e "${YELLOW}╚══════════════════════════════════════════════════════╝${NC}"

    # Track test results
    TESTS_PASSED=0
    TESTS_FAILED=0

    # Registration first
    test_registration && ((TESTS_PASSED++)) || ((TESTS_FAILED++))

    # Run visitor management tests
    test_create_visitor && ((TESTS_PASSED++)) || ((TESTS_FAILED++))

    test_list_visitors && ((TESTS_PASSED++)) || ((TESTS_FAILED++))

    test_get_visitor && ((TESTS_PASSED++)) || ((TESTS_FAILED++))

    test_update_visitor && ((TESTS_PASSED++)) || ((TESTS_FAILED++))

    test_check_in_visitor && ((TESTS_PASSED++)) || ((TESTS_FAILED++))

    test_list_visitors_pending && ((TESTS_PASSED++)) || ((TESTS_FAILED++))

    test_list_visitors_checked_in && ((TESTS_PASSED++)) || ((TESTS_FAILED++))

    test_check_out_visitor && ((TESTS_PASSED++)) || ((TESTS_FAILED++))

    test_delete_visitor && ((TESTS_PASSED++)) || ((TESTS_FAILED++))

    # Summary
    print_header "Test Summary"
    echo -e "${GREEN}Passed: $TESTS_PASSED${NC}"
    echo -e "${RED}Failed: $TESTS_FAILED${NC}"

    if [ $TESTS_FAILED -eq 0 ]; then
        echo -e "\n${GREEN}✅ All Phase 4-A tests passed!${NC}"
        echo -e "\n${YELLOW}Phase 4-A is complete and ready for deployment.${NC}"
        exit 0
    else
        echo -e "\n${RED}❌ Some tests failed. Check output above.${NC}"
        exit 1
    fi
}

# Run main function
main
