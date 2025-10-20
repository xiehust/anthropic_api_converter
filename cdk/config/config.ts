/**
 * Configuration for different deployment environments
 */

export interface EnvironmentConfig {
  // AWS Account and Region
  account?: string;
  region: string;

  // Environment name
  environmentName: string;

  // Platform architecture (set dynamically from CDK_PLATFORM env var)
  platform: 'arm64' | 'amd64';

  // VPC Configuration
  vpcCidr: string;
  maxAzs: number;

  // ECS Configuration
  ecsDesiredCount: number;
  ecsCpu: number;
  ecsMemory: number;
  ecsMinCapacity: number;
  ecsMaxCapacity: number;
  ecsTargetCpuUtilization: number;

  // Container Configuration
  containerPort: number;
  healthCheckPath: string;
  healthCheckInterval: number;
  healthCheckTimeout: number;
  healthCheckHealthyThreshold: number;
  healthCheckUnhealthyThreshold: number;

  // Application Configuration
  requireApiKey: boolean;
  rateLimitEnabled: boolean;
  rateLimitRequests: number;
  rateLimitWindow: number;
  enableMetrics: boolean;

  // DynamoDB Configuration
  dynamodbBillingMode: 'PAY_PER_REQUEST' | 'PROVISIONED';
  dynamodbReadCapacity?: number;
  dynamodbWriteCapacity?: number;

  // Logging Configuration
  logRetentionDays: number;
  enableContainerInsights: boolean;

  // Tags
  tags: { [key: string]: string };
}

// Environment configurations without platform (platform is set at deployment time)
type EnvironmentConfigWithoutPlatform = Omit<EnvironmentConfig, 'platform'>;

export const environments: { [key: string]: EnvironmentConfigWithoutPlatform } = {
  dev: {
    region: process.env.AWS_REGION || 'us-west-2',
    environmentName: 'dev',

    // VPC
    vpcCidr: '10.0.0.0/16',
    maxAzs: 2,

    // ECS
    ecsDesiredCount: 1,
    ecsCpu: 512,
    ecsMemory: 1024,
    ecsMinCapacity: 1,
    ecsMaxCapacity: 2,
    ecsTargetCpuUtilization: 70,

    // Container
    containerPort: 8000,
    healthCheckPath: '/health',
    healthCheckInterval: 30,
    healthCheckTimeout: 10,
    healthCheckHealthyThreshold: 2,
    healthCheckUnhealthyThreshold: 3,

    // Application
    requireApiKey: true,
    rateLimitEnabled: true,
    rateLimitRequests: 100,
    rateLimitWindow: 60,
    enableMetrics: true,

    // DynamoDB
    dynamodbBillingMode: 'PAY_PER_REQUEST',

    // Logging
    logRetentionDays: 7,
    enableContainerInsights: false,

    // Tags
    tags: {
      Environment: 'dev',
      Project: 'anthropic-proxy',
      ManagedBy: 'CDK',
    },
  },

  prod: {
    region: process.env.AWS_REGION || 'us-west-2',
    environmentName: 'prod',

    // VPC
    vpcCidr: '10.1.0.0/16',
    maxAzs: 3,

    // ECS
    ecsDesiredCount: 2,
    ecsCpu: 1024,
    ecsMemory: 2048,
    ecsMinCapacity: 2,
    ecsMaxCapacity: 10,
    ecsTargetCpuUtilization: 70,

    // Container
    containerPort: 8000,
    healthCheckPath: '/health',
    healthCheckInterval: 30,
    healthCheckTimeout: 10,
    healthCheckHealthyThreshold: 2,
    healthCheckUnhealthyThreshold: 3,

    // Application
    requireApiKey: true,
    rateLimitEnabled: true,
    rateLimitRequests: 1000,
    rateLimitWindow: 60,
    enableMetrics: true,

    // DynamoDB
    dynamodbBillingMode: 'PAY_PER_REQUEST',

    // Logging
    logRetentionDays: 30,
    enableContainerInsights: true,

    // Tags
    tags: {
      Environment: 'prod',
      Project: 'anthropic-proxy',
      ManagedBy: 'CDK',
    },
  },
};

export function getConfig(environmentName: string = 'dev'): EnvironmentConfig {
  const config = environments[environmentName];
  if (!config) {
    throw new Error(
      `Unknown environment: ${environmentName}. Available: ${Object.keys(environments).join(', ')}`
    );
  }

  // Get platform from environment variable (set by deploy script)
  const platform = process.env.CDK_PLATFORM as 'arm64' | 'amd64';
  if (!platform || !['arm64', 'amd64'].includes(platform)) {
    throw new Error(
      `Platform must be specified via CDK_PLATFORM environment variable. Valid values: arm64, amd64. Got: ${platform}`
    );
  }

  return {
    ...config,
    platform,
  };
}
