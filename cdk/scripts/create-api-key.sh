#!/bin/bash
set -e

# Script to create API keys in DynamoDB for the Anthropic Proxy

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Default values
ENVIRONMENT="dev"
REGION="${AWS_REGION:-us-west-2}"
USER_ID=""
KEY_NAME=""
RATE_LIMIT=""

# Usage
usage() {
    cat <<EOF
Usage: $0 [OPTIONS]

Create an API key for the Anthropic Proxy service

OPTIONS:
    -e, --environment ENV    Environment (dev|prod) [default: dev]
    -r, --region REGION      AWS region [default: us-west-2]
    -u, --user-id ID         User ID (required)
    -n, --name NAME          Key name/description (required)
    -l, --rate-limit LIMIT   Rate limit requests per minute [optional]
    -h, --help               Show this help message

EXAMPLES:
    # Create a key for a user
    ./scripts/create-api-key.sh -e dev -u user@example.com -n "Production Key"

    # Create a key with rate limit
    ./scripts/create-api-key.sh -e prod -u user@example.com -n "API Key" -l 1000

EOF
    exit 1
}

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        -e|--environment)
            ENVIRONMENT="$2"
            shift 2
            ;;
        -r|--region)
            REGION="$2"
            shift 2
            ;;
        -u|--user-id)
            USER_ID="$2"
            shift 2
            ;;
        -n|--name)
            KEY_NAME="$2"
            shift 2
            ;;
        -l|--rate-limit)
            RATE_LIMIT="$2"
            shift 2
            ;;
        -h|--help)
            usage
            ;;
        *)
            echo -e "${RED}Unknown option: $1${NC}"
            usage
            ;;
    esac
done

# Validate inputs
if [[ -z "$USER_ID" ]]; then
    echo -e "${RED}Error: User ID is required${NC}"
    usage
fi

if [[ -z "$KEY_NAME" ]]; then
    echo -e "${RED}Error: Key name is required${NC}"
    usage
fi

if [[ ! "$ENVIRONMENT" =~ ^(dev|prod)$ ]]; then
    echo -e "${RED}Error: Environment must be 'dev' or 'prod'${NC}"
    exit 1
fi

# Check AWS credentials
if ! aws sts get-caller-identity &> /dev/null; then
    echo -e "${RED}Error: AWS credentials not configured properly.${NC}"
    exit 1
fi

# Generate API key
API_KEY="sk-$(openssl rand -hex 16)"

# Get table name
TABLE_NAME="anthropic-proxy-${ENVIRONMENT}-api-keys"

echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}Creating API Key${NC}"
echo -e "${GREEN}========================================${NC}"
echo -e "Environment: ${YELLOW}${ENVIRONMENT}${NC}"
echo -e "Region: ${YELLOW}${REGION}${NC}"
echo -e "User ID: ${YELLOW}${USER_ID}${NC}"
echo -e "Key Name: ${YELLOW}${KEY_NAME}${NC}"
[ -n "$RATE_LIMIT" ] && echo -e "Rate Limit: ${YELLOW}${RATE_LIMIT} req/min${NC}"
echo -e "${GREEN}========================================${NC}"
echo

# Prepare item
TIMESTAMP=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

ITEM="{
  \"api_key\": {\"S\": \"$API_KEY\"},
  \"user_id\": {\"S\": \"$USER_ID\"},
  \"name\": {\"S\": \"$KEY_NAME\"},
  \"is_active\": {\"BOOL\": true},
  \"created_at\": {\"S\": \"$TIMESTAMP\"},
  \"metadata\": {\"M\": {
    \"created_via\": {\"S\": \"cli\"},
    \"environment\": {\"S\": \"$ENVIRONMENT\"}
  }}
}"

# Add rate limit if specified
if [ -n "$RATE_LIMIT" ]; then
    ITEM=$(echo "$ITEM" | jq --arg limit "$RATE_LIMIT" \
        '.rate_limit = {"N": $limit}')
fi

# Create item in DynamoDB
echo -e "${YELLOW}Creating API key in DynamoDB...${NC}"

if aws dynamodb put-item \
    --table-name "$TABLE_NAME" \
    --item "$ITEM" \
    --region "$REGION" \
    --condition-expression "attribute_not_exists(api_key)" \
    2>/dev/null; then

    echo -e "${GREEN}✓ API key created successfully!${NC}"
    echo
    echo -e "${GREEN}========================================${NC}"
    echo -e "${GREEN}API Key Details${NC}"
    echo -e "${GREEN}========================================${NC}"
    echo -e "${YELLOW}API Key:${NC} $API_KEY"
    echo
    echo -e "${YELLOW}IMPORTANT: Save this API key securely!${NC}"
    echo -e "${YELLOW}It will not be shown again.${NC}"
    echo -e "${GREEN}========================================${NC}"
    echo
    echo -e "${YELLOW}Test the API key:${NC}"
    echo -e "  curl -H 'x-api-key: $API_KEY' https://your-endpoint.com/health"
else
    echo -e "${RED}Error: Failed to create API key${NC}"
    exit 1
fi
