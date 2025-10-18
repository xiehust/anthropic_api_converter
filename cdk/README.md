# Anthropic-Bedrock API Proxy - CDK Infrastructure

This directory contains AWS CDK infrastructure code for deploying the Anthropic-Bedrock API Proxy to AWS ECS Fargate with CloudFront distribution.

## Architecture

```
Internet
    ↓
CloudFront Distribution (Global CDN)
    ↓ (Max timeout: 60s)
Application Load Balancer (Regional)
    ↓
ECS Fargate Service (Multi-AZ)
    ↓
Container Tasks (Auto-scaling)
    ↓
├─→ AWS Bedrock (Anthropic models)
└─→ DynamoDB (API keys, usage, cache)
```

### Key Features

- **CloudFront CDN**: Global edge caching with max timeout (60s), custom header forwarding
- **Application Load Balancer**: Health checks, target group management
- **ECS Fargate**: Serverless container orchestration with auto-scaling
- **DynamoDB**: Four tables for API keys, usage tracking, caching, and model mapping
- **VPC**: Multi-AZ deployment with public/private subnets
- **Security**: WAF rules, security groups, IAM roles with least privilege
- **Monitoring**: CloudWatch logs, Container Insights, metrics

## Prerequisites

### Required Tools

1. **AWS CLI** (v2.x or later)
   ```bash
   aws --version
   ```

2. **Node.js** (v18.x or later) and npm
   ```bash
   node --version
   npm --version
   ```

3. **Docker** (for building container images)
   ```bash
   docker --version
   ```

4. **AWS CDK** (installed automatically via npm)

### AWS Account Setup

1. **Configure AWS Credentials**
   ```bash
   aws configure
   # Or use environment variables:
   export AWS_ACCESS_KEY_ID=your-access-key
   export AWS_SECRET_ACCESS_KEY=your-secret-key
   export AWS_REGION=us-west-2
   ```

2. **Bootstrap CDK** (one-time per account/region)
   ```bash
   cd cdk
   npm install
   npx cdk bootstrap aws://ACCOUNT-ID/REGION
   ```

3. **Required IAM Permissions**
   Your AWS user/role needs permissions for:
   - CloudFormation (full)
   - ECS, ECR (full)
   - EC2, VPC (full)
   - DynamoDB (full)
   - CloudFront, WAF (full)
   - IAM (role creation)
   - Secrets Manager (create/read secrets)
   - CloudWatch Logs (create/write)

## Quick Start

### 1. Install Dependencies

```bash
cd cdk
npm install
```

### 2. Deploy to Development

```bash
./scripts/deploy.sh -e dev -r us-west-2
```

This will deploy:
- DynamoDB tables
- VPC with NAT gateways
- ECS Fargate cluster and service
- Application Load Balancer
- CloudFront distribution

Deployment takes approximately **15-20 minutes**.

### 3. Create an API Key

```bash
./scripts/create-api-key.sh \
  -e dev \
  -u user@example.com \
  -n "My API Key" \
  -l 1000
```

Save the generated API key securely - it won't be shown again.

### 4. Test the Deployment

```bash
# Get the CloudFront URL from deployment output
CLOUDFRONT_URL="https://d1234567890.cloudfront.net"

# Test health endpoint
curl "${CLOUDFRONT_URL}/health"

# Test with API key
curl -X POST "${CLOUDFRONT_URL}/v1/messages" \
  -H "x-api-key: sk-your-api-key" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "anthropic.claude-3-5-sonnet-20241022-v2:0",
    "max_tokens": 100,
    "messages": [
      {"role": "user", "content": "Hello!"}
    ]
  }'
```

## Configuration

### Environment Configuration

Edit `config/config.ts` to customize environments:

```typescript
export const environments = {
  dev: {
    region: 'us-west-2',
    ecsDesiredCount: 1,
    ecsMinCapacity: 1,
    ecsMaxCapacity: 2,
    // ... more settings
  },
  prod: {
    region: 'us-west-2',
    ecsDesiredCount: 2,
    ecsMinCapacity: 2,
    ecsMaxCapacity: 10,
    // ... more settings
  },
};
```

### Key Configuration Options

| Setting | Dev Default | Prod Default | Description |
|---------|-------------|--------------|-------------|
| `ecsDesiredCount` | 1 | 2 | Initial number of tasks |
| `ecsCpu` | 512 | 1024 | CPU units per task |
| `ecsMemory` | 1024 | 2048 | Memory (MB) per task |
| `ecsMinCapacity` | 1 | 2 | Min auto-scaling tasks |
| `ecsMaxCapacity` | 2 | 10 | Max auto-scaling tasks |
| `maxAzs` | 2 | 3 | Availability zones |
| `enableCloudFront` | true | true | Enable CloudFront |
| `dynamodbBillingMode` | PAY_PER_REQUEST | PAY_PER_REQUEST | DynamoDB billing |

## CloudFront Configuration

### Timeout Settings

CloudFront has a **maximum timeout of 60 seconds** for origin requests:
- `readTimeout`: 60 seconds (maximum allowed)
- `keepaliveTimeout`: 60 seconds (maximum allowed)

For long-running streaming requests:
1. Client must handle streaming responses
2. Server sends data within 60-second intervals
3. Connection remains open as long as data flows

### Header Forwarding

The following headers are forwarded to the origin:

**Required for Anthropic API:**
- `x-api-key` - API authentication
- `anthropic-version` - API version
- `anthropic-beta` - Beta features
- `content-type` - Request content type
- `accept` - Response content type

**Standard Headers:**
- `authorization` - Alternative auth header
- `user-agent` - Client identification
- `origin` - CORS origin
- `referer` - Request referer

All query strings and request methods are forwarded.

### Cache Policy

API requests are **not cached** to ensure fresh responses:
- `defaultTtl`: 0 seconds
- `minTtl`: 0 seconds
- `maxTtl`: 1 second

## Deployment Options

### Deploy to Production

```bash
./scripts/deploy.sh -e prod -r us-west-2
```

**Production differences:**
- More ECS tasks (2-10 auto-scaling)
- 3 availability zones
- Container Insights enabled
- 30-day log retention
- WAF enabled with rate limiting
- VPC endpoints for cost optimization
- Deletion protection on resources

### Deploy to Specific Region

```bash
./scripts/deploy.sh -e prod -r us-east-1
```

**Note:** CloudFront stacks are always deployed to `us-east-1` (AWS requirement).

### Skip Build Step

```bash
./scripts/deploy.sh -e dev -s
```

Useful for quick redeployments when dependencies haven't changed.

### Destroy Infrastructure

```bash
./scripts/deploy.sh -e dev -d
```

**Warning:** This deletes all resources. DynamoDB tables with `RETAIN` policy must be deleted manually.

## Stack Details

### 1. DynamoDB Stack

Creates four tables:

#### API Keys Table
- **Table Name:** `anthropic-proxy-{env}-api-keys`
- **Primary Key:** `api_key` (String)
- **GSI:** `user_id-index` for querying by user
- **Attributes:** `api_key`, `user_id`, `name`, `is_active`, `rate_limit`, `metadata`
- **Encryption:** AWS-managed
- **Backup:** Point-in-time recovery (prod only)

#### Usage Table
- **Table Name:** `anthropic-proxy-{env}-usage`
- **Primary Key:** `api_key` (String), `timestamp` (String)
- **GSI:** `request_id-index` for request lookups
- **TTL:** Automatic expiration via `ttl` attribute
- **Purpose:** Track API usage and costs

#### Cache Table
- **Table Name:** `anthropic-proxy-{env}-cache`
- **Primary Key:** `cache_key` (String)
- **TTL:** Automatic expiration via `ttl` attribute
- **Purpose:** Response caching

#### Model Mapping Table
- **Table Name:** `anthropic-proxy-{env}-model-mapping`
- **Primary Key:** `anthropic_model_id` (String)
- **Purpose:** Map Anthropic model IDs to Bedrock ARNs

### 2. Network Stack

Creates VPC infrastructure:

- **VPC CIDR:** `10.0.0.0/16` (dev), `10.1.0.0/16` (prod)
- **Subnets:**
  - Public subnets (for ALB)
  - Private subnets with NAT (for ECS tasks)
- **NAT Gateways:** 1 (dev), 3 (prod)
- **VPC Endpoints (prod only):**
  - S3 Gateway Endpoint (free)
  - DynamoDB Gateway Endpoint (free)
  - ECR, CloudWatch Logs, Bedrock Runtime (interface endpoints)

**Security Groups:**
- ALB SG: Allows 80, 443 from internet
- ECS SG: Allows traffic from ALB only

### 3. ECS Stack

Creates container orchestration:

**ECS Cluster:**
- **Name:** `anthropic-proxy-{env}`
- **Container Insights:** Enabled in prod
- **Capacity Provider:** Fargate Spot (optional)

**Task Definition:**
- **CPU:** 512 (dev), 1024 (prod)
- **Memory:** 1024 MB (dev), 2048 MB (prod)
- **Container Image:** Built from Dockerfile in project root
- **Health Check:** `/health` endpoint every 30s
- **Environment Variables:**
  - AWS region, DynamoDB table names
  - Feature flags, rate limits
  - Secrets Manager integration for master API key

**Service:**
- **Desired Count:** 1 (dev), 2 (prod)
- **Auto-scaling:**
  - CPU-based: 70% target
  - Memory-based: 70% target
  - Request count: 1000 per target
- **Deployment:** Rolling updates with circuit breaker
- **Execute Command:** Enabled in dev for debugging

**Application Load Balancer:**
- **Scheme:** Internet-facing
- **Health Check:** `/health` every 30s
- **Deregistration Delay:** 30 seconds
- **Deletion Protection:** Enabled in prod

### 4. CloudFront Stack

Creates global CDN:

**Distribution:**
- **Price Class:** `PriceClass_100` (dev), `PriceClass_All` (prod)
- **HTTP Version:** HTTP/2 and HTTP/3
- **Compression:** Enabled (gzip, brotli)
- **Logging:** Enabled in prod

**Origin Configuration:**
- **Origin:** ALB (HTTP only)
- **Timeout:** 60 seconds (maximum)
- **Connection Attempts:** 3
- **Connection Timeout:** 10 seconds

**WAF (prod only):**
- Rate limiting: 2000 req/5min per IP
- AWS Managed Rules: Common Rule Set
- AWS Managed Rules: Known Bad Inputs

## IAM Roles and Permissions

### Task Execution Role

Used by ECS to start containers:
- Pull images from ECR
- Write logs to CloudWatch
- Retrieve secrets from Secrets Manager

### Task Role

Used by application containers:
- **DynamoDB:** Read/write to all four tables
- **Bedrock:** Invoke models (`bedrock:InvokeModel`, `bedrock:InvokeModelWithResponseStream`)
- **CloudWatch Logs:** Create log streams and write logs

## Secrets Management

### Master API Key

The master API key is stored in AWS Secrets Manager:

```bash
# Get secret name from stack output
SECRET_NAME="anthropic-proxy-dev-master-api-key"

# Retrieve the key
aws secretsmanager get-secret-value \
  --secret-id "$SECRET_NAME" \
  --region us-west-2 \
  --query 'SecretString' \
  --output text | jq -r '.password'
```

The master key:
- Bypasses DynamoDB authentication
- Has no rate limits
- Should only be used for admin operations

## Monitoring and Logs

### CloudWatch Logs

Log Group: `/ecs/anthropic-proxy-{env}`

```bash
# View recent logs
aws logs tail /ecs/anthropic-proxy-dev --follow

# Filter for errors
aws logs filter-log-events \
  --log-group-name /ecs/anthropic-proxy-dev \
  --filter-pattern "ERROR"
```

### Container Insights (prod only)

View metrics in CloudWatch Console:
- ECS → Clusters → anthropic-proxy-prod → Metrics
- CPU, Memory, Network utilization
- Task count, running tasks

### CloudFront Metrics

- Requests, Bytes downloaded
- 4xx/5xx Error rates
- Cache hit ratio (should be near 0% for API)

## Cost Optimization

### Development Environment

**Estimated monthly cost:** ~$50-100

- ECS Fargate: 1 task × 0.5 vCPU × 1GB RAM
- ALB: 1 load balancer + LCU usage
- NAT Gateway: 1 gateway + data transfer
- DynamoDB: Pay-per-request (minimal)
- CloudFront: First 1TB free, then $0.085/GB
- CloudWatch: Logs and metrics

### Production Environment

**Estimated monthly cost:** ~$200-500 (base) + usage

- ECS Fargate: 2-10 tasks (auto-scaling)
- ALB: Higher LCU usage
- NAT Gateways: 3 (multi-AZ)
- VPC Endpoints: ~$7 each
- DynamoDB: Pay-per-request (scales with usage)
- CloudFront: $0.085/GB (US/Europe)
- WAF: $5/month + $1 per rule + $0.60 per million requests
- Container Insights: ~$0.30 per GB ingested

**Cost Optimization Tips:**
1. Use VPC endpoints to avoid NAT gateway charges for AWS API calls
2. Enable DynamoDB auto-scaling for predictable traffic
3. Use CloudFront caching for static content (not API responses)
4. Review CloudWatch log retention (7 days dev, 30 days prod)
5. Use Fargate Spot for non-critical workloads (70% discount)

## Troubleshooting

### Deployment Fails

**Issue:** CDK bootstrap not found
```bash
npx cdk bootstrap aws://ACCOUNT-ID/REGION
```

**Issue:** Docker image build fails
```bash
# Build locally first
docker build -t anthropic-proxy:test .
```

**Issue:** Insufficient IAM permissions
- Check your AWS user/role has required permissions
- Review CloudFormation stack events for specific errors

### Container Fails to Start

**Check logs:**
```bash
aws logs tail /ecs/anthropic-proxy-dev --follow
```

**Common issues:**
- Missing environment variables
- DynamoDB tables not accessible
- Bedrock permissions missing
- Container health check failing

**Debug with ECS Exec (dev only):**
```bash
aws ecs execute-command \
  --cluster anthropic-proxy-dev \
  --task TASK-ID \
  --container anthropic-proxy \
  --interactive \
  --command "/bin/bash"
```

### Health Check Failures

ALB health checks fail if:
- Container port mismatch (should be 8000)
- `/health` endpoint not responding
- Security group blocking traffic
- Container taking too long to start (increase grace period)

### CloudFront 504 Errors

CloudFront returns 504 if:
- Origin takes > 60 seconds to respond
- Origin is unhealthy
- Network connectivity issues

**Solutions:**
- Check ALB target health
- Review ECS task logs
- Verify security groups allow traffic
- For streaming: ensure data sent within 60s intervals

## Advanced Configuration

### Custom Domain with HTTPS

1. **Create ACM certificate in us-east-1:**
   ```bash
   aws acm request-certificate \
     --domain-name api.yourdomain.com \
     --validation-method DNS \
     --region us-east-1
   ```

2. **Update CloudFront stack:**
   ```typescript
   certificate: acm.Certificate.fromCertificateArn(...),
   domainNames: ['api.yourdomain.com'],
   ```

3. **Create Route53 record:**
   ```typescript
   new route53.ARecord(this, 'AliasRecord', {
     zone: hostedZone,
     target: route53.RecordTarget.fromAlias(
       new targets.CloudFrontTarget(distribution)
     ),
   });
   ```

### Using Fargate Spot

Save 70% on compute costs:

```typescript
const capacityProviderStrategy = [
  {
    capacityProvider: 'FARGATE_SPOT',
    weight: 1,
    base: 0,
  },
  {
    capacityProvider: 'FARGATE',
    weight: 0,
    base: 1, // Keep at least 1 on-demand task
  },
];
```

**Note:** Spot tasks can be interrupted with 2-minute notice.

### Blue/Green Deployments

Use CodeDeploy for zero-downtime deployments:

```typescript
import * as codedeploy from 'aws-cdk-lib/aws-codedeploy';

const deploymentConfig = new codedeploy.EcsDeploymentConfig(this, 'Config', {
  trafficRouting: codedeploy.TimeBasedLinearTrafficRouting.allAtOnce(),
});
```

## Maintenance

### Update Container Image

1. Build new image
2. Push to ECR
3. CDK will automatically deploy new version

```bash
./scripts/deploy.sh -e prod
```

ECS performs rolling update with circuit breaker.

### Rotate Master API Key

```bash
# Generate new secret value
aws secretsmanager update-secret \
  --secret-id anthropic-proxy-prod-master-api-key \
  --secret-string '{"password": "new-key-value"}'

# Restart ECS tasks to pick up new value
aws ecs update-service \
  --cluster anthropic-proxy-prod \
  --service anthropic-proxy-prod \
  --force-new-deployment
```

### Scale ECS Service

```bash
# Manual scaling
aws ecs update-service \
  --cluster anthropic-proxy-prod \
  --service anthropic-proxy-prod \
  --desired-count 5

# Or update config.ts and redeploy
```

### View DynamoDB Items

```bash
# List API keys
aws dynamodb scan \
  --table-name anthropic-proxy-dev-api-keys \
  --region us-west-2

# Get specific API key
aws dynamodb get-item \
  --table-name anthropic-proxy-dev-api-keys \
  --key '{"api_key": {"S": "sk-..."}}' \
  --region us-west-2
```

## Security Best Practices

1. **Rotate Secrets:** Rotate master API key quarterly
2. **Review IAM Roles:** Use least privilege principle
3. **Enable WAF:** Always enable in production
4. **Monitor Logs:** Set up CloudWatch alarms for errors
5. **Update Dependencies:** Keep CDK and container dependencies up to date
6. **Use Secrets Manager:** Never hardcode credentials
7. **Enable Encryption:** Use AWS-managed keys for DynamoDB, S3
8. **Network Isolation:** ECS tasks in private subnets only

## Support

For issues or questions:
1. Check CloudWatch logs
2. Review CloudFormation events
3. Consult [AWS CDK Documentation](https://docs.aws.amazon.com/cdk/)
4. Review project ARCHITECTURE.md for application details

## License

See main project LICENSE file.
