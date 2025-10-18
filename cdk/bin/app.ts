#!/usr/bin/env node
import 'source-map-support/register';
import * as cdk from 'aws-cdk-lib';
import { DynamoDBStack } from '../lib/dynamodb-stack';
import { NetworkStack } from '../lib/network-stack';
import { ECSStack } from '../lib/ecs-stack';
import { CloudFrontStack } from '../lib/cloudfront-stack';
import { getConfig } from '../config/config';

const app = new cdk.App();

// Get environment from context
const environmentName = app.node.tryGetContext('environment') || 'dev';
const config = getConfig(environmentName);

console.log(`Deploying to environment: ${environmentName}`);
console.log(`Region: ${config.region}`);

// Stack naming
const stackPrefix = `AnthropicProxy-${config.environmentName}`;

// Environment configuration
const env = {
  account: config.account || process.env.CDK_DEFAULT_ACCOUNT,
  region: config.region,
};

// Deploy DynamoDB Stack
const dynamoDBStack = new DynamoDBStack(app, `${stackPrefix}-DynamoDB`, {
  env,
  config,
  stackName: `${stackPrefix}-DynamoDB`,
  description: `DynamoDB tables for Anthropic proxy ${config.environmentName}`,
  tags: config.tags,
});

// Deploy Network Stack
const networkStack = new NetworkStack(app, `${stackPrefix}-Network`, {
  env,
  config,
  stackName: `${stackPrefix}-Network`,
  description: `VPC and networking for Anthropic proxy ${config.environmentName}`,
  tags: config.tags,
});

// Deploy ECS Stack
const ecsStack = new ECSStack(app, `${stackPrefix}-ECS`, {
  env,
  config,
  vpc: networkStack.vpc,
  albSecurityGroup: networkStack.albSecurityGroup,
  ecsSecurityGroup: networkStack.ecsSecurityGroup,
  apiKeysTable: dynamoDBStack.apiKeysTable,
  usageTable: dynamoDBStack.usageTable,
  cacheTable: dynamoDBStack.cacheTable,
  modelMappingTable: dynamoDBStack.modelMappingTable,
  stackName: `${stackPrefix}-ECS`,
  description: `ECS Fargate cluster and service for Anthropic proxy ${config.environmentName}`,
  tags: config.tags,
});

// Add dependencies
ecsStack.addDependency(dynamoDBStack);
ecsStack.addDependency(networkStack);

// Deploy CloudFront Stack (if enabled)
if (config.enableCloudFront) {
  const cloudFrontStack = new CloudFrontStack(app, `${stackPrefix}-CloudFront`, {
    env: {
      account: env.account,
      region: 'us-east-1', // CloudFront resources must be in us-east-1
    },
    config,
    alb: ecsStack.alb,
    stackName: `${stackPrefix}-CloudFront`,
    description: `CloudFront distribution for Anthropic proxy ${config.environmentName}`,
    tags: config.tags,
    crossRegionReferences: true,
  });

  cloudFrontStack.addDependency(ecsStack);
}

// Add tags to all stacks
Object.entries(config.tags).forEach(([key, value]) => {
  cdk.Tags.of(app).add(key, value);
});

app.synth();
