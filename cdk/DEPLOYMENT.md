# Quick Deployment Guide

This is a quick reference for deploying the Anthropic Proxy to AWS. For detailed documentation, see [README.md](README.md).

## Prerequisites Checklist

- [ ] AWS CLI installed and configured
- [ ] Node.js 18+ and npm installed
- [ ] Docker installed and running
- [ ] AWS account with appropriate permissions
- [ ] CDK bootstrapped in target region

## Quick Deploy (Development)

```bash
# 1. Install dependencies
cd cdk
npm install

# 2. Bootstrap CDK (first time only)
export AWS_REGION=us-west-2
npx cdk bootstrap

# 3. Deploy all stacks (ARM64 recommended for better price-performance)
./scripts/deploy.sh -e dev -r us-west-2 -p arm64

# 4. Create an API key
./scripts/create-api-key.sh -e dev -u admin@example.com -n "Dev Key"

# 5. Test deployment
curl http://YOUR_ALB_URL/health
```

**Time:** ~15-20 minutes for initial deployment

## Platform Selection

Choose your target architecture:

- **`-p arm64`** (Recommended): AWS Graviton2 processors
  - 20% cheaper than x86
  - 40% better price-performance
  - Best for most workloads

- **`-p amd64`**: Traditional x86_64 architecture
  - Use if you have specific x86 requirements
  - Slightly higher cost

## Quick Deploy (Production)

```bash
# Deploy to production with higher capacity and ARM64
./scripts/deploy.sh -e prod -r us-west-2 -p arm64

# Or deploy with AMD64 if needed
./scripts/deploy.sh -e prod -r us-west-2 -p amd64
```

**Differences from dev:**
- 2-10 ECS tasks (auto-scaling)
- 3 availability zones
- WAF enabled
- Container Insights enabled
- 30-day log retention

## Common Commands

```bash
# View synthesized CloudFormation
npx cdk synth -c environment=dev

# Show differences before deploy
npx cdk diff -c environment=dev

# Deploy specific stack only
npx cdk deploy AnthropicProxy-dev-ECS -c environment=dev

# Destroy all resources
./scripts/deploy.sh -e dev -d

# View logs
aws logs tail /ecs/anthropic-proxy-dev --follow

# Get master API key
aws secretsmanager get-secret-value \
  --secret-id anthropic-proxy-dev-master-api-key \
  --query 'SecretString' --output text | jq -r '.password'
```

## Stack Deployment Order

CDK automatically handles dependencies, but stacks are deployed in this order:

1. **DynamoDB Stack** - Creates database tables
2. **Network Stack** - Creates VPC, subnets, security groups
3. **ECS Stack** - Creates cluster, service, ALB, container tasks

## Configuration Quick Reference

Edit `config/config.ts` to customize:

```typescript
{
  // Region
  region: 'us-west-2',

  // ECS capacity
  ecsDesiredCount: 1,        // Initial task count
  ecsMinCapacity: 1,         // Min auto-scaling
  ecsMaxCapacity: 2,         // Max auto-scaling

  // Resources
  ecsCpu: 512,               // CPU units
  ecsMemory: 1024,           // Memory in MB

  // VPC
  maxAzs: 2,                 // Availability zones

  // Features
  enableMetrics: true,       // Prometheus metrics
  requireApiKey: true,       // API key auth
}
```

## Outputs After Deployment

The deployment script shows:

```
Access URLs:
  ALB: http://anthropic-proxy-dev-alb-123456789.us-west-2.elb.amazonaws.com

Master API Key Secret:
  Secret Name: anthropic-proxy-dev-master-api-key
  Retrieve with: aws secretsmanager get-secret-value --secret-id ...

Next Steps:
  1. Create API keys using: ./scripts/create-api-key.sh
  2. Test the health endpoint
  3. Review CloudWatch logs
```

## Testing the Deployment

### 1. Health Check

```bash
ENDPOINT="http://YOUR_ALB_URL"
curl "${ENDPOINT}/health"
```

Expected response:
```json
{
  "status": "healthy",
  "version": "1.0.0",
  "services": {
    "bedrock": {"status": "available"},
    "dynamodb": {"status": "available"}
  }
}
```

### 2. List Models

```bash
curl -H "x-api-key: YOUR_API_KEY" \
  "${ENDPOINT}/v1/models"
```

### 3. Send Message

```bash
curl -X POST "${ENDPOINT}/v1/messages" \
  -H "x-api-key: YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "anthropic.claude-3-5-sonnet-20241022-v2:0",
    "max_tokens": 100,
    "messages": [
      {"role": "user", "content": "Hello!"}
    ]
  }'
```

## Troubleshooting Quick Fixes

### Deployment fails with "CDK not bootstrapped"

```bash
npx cdk bootstrap aws://$(aws sts get-caller-identity --query Account --output text)/us-west-2
```

### Container won't start

```bash
# Check logs
aws logs tail /ecs/anthropic-proxy-dev --follow

# Common fixes:
# - Verify DynamoDB table names in environment variables
# - Check IAM role permissions for Bedrock and DynamoDB
# - Ensure Docker image builds successfully locally
```

### ALB health checks failing

```bash
# Check ECS task status
aws ecs describe-services \
  --cluster anthropic-proxy-dev \
  --services anthropic-proxy-dev

# Verify security groups allow traffic
# Check container logs for errors
# Increase health check grace period in config
```

### 504 Gateway Timeout

ALB may timeout for long-running requests. For long-running requests:
- Use streaming responses
- Ensure data is sent within reasonable intervals
- Check if ECS tasks are responding
- Consider increasing ALB timeout if needed

## Cost Estimates

### Development
- **~$50-100/month**
- 1 ECS task, 1 NAT gateway, minimal traffic

### Production
- **~$200-500/month base + usage**
- 2-10 ECS tasks (auto-scaling)
- 3 NAT gateways (multi-AZ)
- VPC endpoints, WAF, Container Insights

See [README.md](README.md#cost-optimization) for detailed breakdown.

## Updating the Deployment

### Update Container Image

```bash
# Make changes to application code
# Redeploy (CDK will rebuild and deploy new image)
./scripts/deploy.sh -e dev -r us-west-2
```

ECS performs rolling update automatically.

### Update Infrastructure

```bash
# Edit config/config.ts or stack files
# Deploy changes
./scripts/deploy.sh -e dev -r us-west-2
```

### Scale Service

```bash
# Via AWS CLI
aws ecs update-service \
  --cluster anthropic-proxy-dev \
  --service anthropic-proxy-dev \
  --desired-count 3

# Or update config.ts and redeploy
```

## Clean Up

### Destroy All Resources

```bash
./scripts/deploy.sh -e dev -d
```

**Note:** Some resources have deletion protection:
- DynamoDB tables (RETAIN policy in prod)
- ALB (deletion protection in prod)

Delete manually if needed:
```bash
aws dynamodb delete-table --table-name anthropic-proxy-prod-api-keys
aws elbv2 modify-load-balancer-attributes \
  --load-balancer-arn ARN \
  --attributes Key=deletion_protection.enabled,Value=false
```

## Getting Help

1. **Check logs:** `aws logs tail /ecs/anthropic-proxy-dev --follow`
2. **Review events:** CloudFormation console → Stack → Events
3. **View metrics:** CloudWatch console → ECS cluster metrics
4. **Documentation:** See [README.md](README.md) for detailed guide

## Next Steps

After deployment:

1. **Set up monitoring alerts:**
   - ECS task failures
   - ALB 5xx errors
   - High CPU/memory utilization

2. **Configure custom domain:**
   - Create ACM certificate
   - Add HTTPS listener to ALB
   - Add Route53 DNS record

3. **Set up CI/CD:**
   - Use GitHub Actions or CodePipeline
   - Automate deployments on git push
   - Run integration tests

4. **Enable additional security:**
   - VPC Flow Logs
   - CloudTrail logging
   - GuardDuty monitoring

5. **Optimize costs:**
   - Review CloudWatch retention
   - Enable Fargate Spot
   - Set up DynamoDB auto-scaling
