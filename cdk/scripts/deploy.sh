#!/bin/bash
set -e

# Anthropic Proxy CDK Deployment Script
# This script deploys the Anthropic-Bedrock API proxy to AWS

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Default values
ENVIRONMENT="prod"
REGION="${AWS_REGION:-us-west-2}"
PLATFORM=""
SKIP_BUILD=false
DESTROY=false

# Usage
usage() {
    cat <<EOF
Usage: $0 [OPTIONS]

Deploy Anthropic-Bedrock API Proxy to AWS using CDK

OPTIONS:
    -e, --environment ENV    Environment to deploy (dev|prod) [default: prod]
    -r, --region REGION      AWS region [default: us-west-2]
    -p, --platform PLATFORM  Platform architecture (arm64|amd64) [REQUIRED]
    -s, --skip-build         Skip npm install and build
    -d, --destroy            Destroy the stack instead of deploying
    -h, --help               Show this help message

EXAMPLES:
    # Deploy to dev environment with ARM64 (Graviton)
    ./scripts/deploy.sh -e dev -p arm64

    # Deploy to prod with AMD64 in us-east-1
    ./scripts/deploy.sh -e prod -r us-east-1 -p amd64

    # Destroy dev environment
    ./scripts/deploy.sh -e dev -p arm64 -d

PREREQUISITES:
    - AWS CLI configured with appropriate credentials
    - Node.js and npm installed
    - Docker installed (for building container images)
    - CDK bootstrapped in target account/region

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
        -p|--platform)
            PLATFORM="$2"
            shift 2
            ;;
        -s|--skip-build)
            SKIP_BUILD=true
            shift
            ;;
        -d|--destroy)
            DESTROY=true
            shift
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

# Validate platform (required)
if [[ -z "$PLATFORM" ]]; then
    echo -e "${RED}Error: Platform is required. Use -p arm64 or -p amd64${NC}"
    usage
fi

if [[ ! "$PLATFORM" =~ ^(arm64|amd64)$ ]]; then
    echo -e "${RED}Error: Platform must be 'arm64' or 'amd64'${NC}"
    exit 1
fi

# Validate environment
if [[ ! "$ENVIRONMENT" =~ ^(dev|prod)$ ]]; then
    echo -e "${RED}Error: Environment must be 'dev' or 'prod'${NC}"
    exit 1
fi

echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}Anthropic Proxy Deployment${NC}"
echo -e "${GREEN}========================================${NC}"
echo -e "Environment: ${YELLOW}${ENVIRONMENT}${NC}"
echo -e "Region: ${YELLOW}${REGION}${NC}"
echo -e "Platform: ${YELLOW}${PLATFORM}${NC}"
echo -e "Action: ${YELLOW}$([ "$DESTROY" = true ] && echo "DESTROY" || echo "DEPLOY")${NC}"
echo -e "${GREEN}========================================${NC}"
echo

# Check prerequisites
echo -e "${YELLOW}Checking prerequisites...${NC}"

if ! command -v aws &> /dev/null; then
    echo -e "${RED}Error: AWS CLI not found. Please install AWS CLI.${NC}"
    exit 1
fi

if ! command -v node &> /dev/null; then
    echo -e "${RED}Error: Node.js not found. Please install Node.js.${NC}"
    exit 1
fi

if ! command -v docker &> /dev/null; then
    echo -e "${RED}Error: Docker not found. Please install Docker.${NC}"
    exit 1
fi

# Check AWS credentials
if ! aws sts get-caller-identity &> /dev/null; then
    echo -e "${RED}Error: AWS credentials not configured properly.${NC}"
    exit 1
fi

ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
echo -e "${GREEN}✓ AWS Account: ${ACCOUNT_ID}${NC}"

# Check if CDK is bootstrapped
echo -e "${YELLOW}Checking CDK bootstrap status...${NC}"
if ! aws cloudformation describe-stacks --stack-name CDKToolkit --region "$REGION" &> /dev/null; then
    echo -e "${YELLOW}Warning: CDK not bootstrapped in ${REGION}.${NC}"
    echo -e "${YELLOW}Run: cdk bootstrap aws://${ACCOUNT_ID}/${REGION}${NC}"
    read -p "Do you want to bootstrap now? (y/n) " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        cd "$(dirname "$0")/.." || exit 1
        npx cdk bootstrap "aws://${ACCOUNT_ID}/${REGION}"
    else
        echo -e "${RED}Cannot proceed without CDK bootstrap.${NC}"
        exit 1
    fi
fi
echo -e "${GREEN}✓ CDK bootstrapped${NC}"

# Navigate to CDK directory
cd "$(dirname "$0")/.." || exit 1

# Install dependencies and build
if [ "$SKIP_BUILD" = false ]; then
    echo -e "${YELLOW}Installing dependencies...${NC}"
    npm install
    echo -e "${GREEN}✓ Dependencies installed${NC}"

    echo -e "${YELLOW}Building CDK project...${NC}"
    npm run build
    echo -e "${GREEN}✓ Build complete${NC}"
fi

# Export environment variables
export AWS_REGION="$REGION"
export CDK_DEFAULT_REGION="$REGION"
export CDK_DEFAULT_ACCOUNT="$ACCOUNT_ID"
export CDK_ENVIRONMENT="$ENVIRONMENT"
export CDK_PLATFORM="$PLATFORM"

# Clean cdk.out directory to prevent ENAMETOOLONG errors
echo -e "${YELLOW}Cleaning previous CDK output...${NC}"
rm -rf cdk.out
echo -e "${GREEN}✓ Cleanup complete${NC}"

# Perform action
if [ "$DESTROY" = true ]; then
    echo -e "${RED}========================================${NC}"
    echo -e "${RED}WARNING: DESTROYING INFRASTRUCTURE${NC}"
    echo -e "${RED}========================================${NC}"
    echo -e "${RED}This will delete all resources in the ${ENVIRONMENT} environment.${NC}"
    read -p "Are you sure? Type 'yes' to confirm: " CONFIRM

    if [ "$CONFIRM" != "yes" ]; then
        echo -e "${YELLOW}Destroy cancelled.${NC}"
        exit 0
    fi

    echo -e "${YELLOW}Destroying stacks...${NC}"
    npx cdk destroy --all -c environment="$ENVIRONMENT" --force
    echo -e "${GREEN}✓ Infrastructure destroyed${NC}"
else
    echo -e "${YELLOW}Synthesizing CloudFormation templates...${NC}"
    npx cdk synth -c environment="$ENVIRONMENT"
    echo -e "${GREEN}✓ Synthesis complete${NC}"

    echo -e "${YELLOW}Deploying stacks...${NC}"
    npx cdk deploy --all -c environment="$ENVIRONMENT" --require-approval never

    echo -e "${GREEN}========================================${NC}"
    echo -e "${GREEN}Deployment Complete!${NC}"
    echo -e "${GREEN}========================================${NC}"

    # Get outputs
    echo -e "${YELLOW}Retrieving stack outputs...${NC}"

    ALB_DNS=$(aws cloudformation describe-stacks \
        --stack-name "AnthropicProxy-${ENVIRONMENT}-ECS" \
        --region "$REGION" \
        --query 'Stacks[0].Outputs[?OutputKey==`ALBDNSName`].OutputValue' \
        --output text 2>/dev/null || echo "N/A")

    CLOUDFRONT_URL=$(aws cloudformation describe-stacks \
        --stack-name "AnthropicProxy-${ENVIRONMENT}-CloudFront" \
        --region us-east-1 \
        --query 'Stacks[0].Outputs[?OutputKey==`DistributionURL`].OutputValue' \
        --output text 2>/dev/null || echo "N/A")

    SECRET_NAME=$(aws cloudformation describe-stacks \
        --stack-name "AnthropicProxy-${ENVIRONMENT}-ECS" \
        --region "$REGION" \
        --query 'Stacks[0].Outputs[?OutputKey==`MasterAPIKeySecretName`].OutputValue' \
        --output text 2>/dev/null || echo "N/A")

    echo
    echo -e "${GREEN}Access URLs:${NC}"
    echo -e "  ALB: http://${ALB_DNS}"
    [ "$CLOUDFRONT_URL" != "N/A" ] && echo -e "  CloudFront: ${CLOUDFRONT_URL}"
    echo
    echo -e "${GREEN}Master API Key Secret:${NC}"
    echo -e "  Secret Name: ${SECRET_NAME}"
    echo -e "  Retrieve with: aws secretsmanager get-secret-value --secret-id ${SECRET_NAME} --region ${REGION}"
    echo
    echo -e "${YELLOW}Next Steps:${NC}"
    echo -e "  1. Create API keys using: ./scripts/create-api-key.sh"
    echo -e "  2. Test the health endpoint: curl http://${ALB_DNS}/health"
    echo -e "  3. Review CloudWatch logs in the AWS Console"
fi

echo -e "${GREEN}========================================${NC}"
