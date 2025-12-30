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

  // Launch type (set dynamically from CDK_LAUNCH_TYPE env var)
  // - fargate: Serverless, no Docker access (default)
  // - ec2: EC2 instances, supports Docker socket for PTC
  launchType: 'fargate' | 'ec2';

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

  // EC2 Launch Type Configuration (only used when launchType is 'ec2')
  ec2InstanceType: string;           // e.g., 't3.medium', 'c6g.large'
  ec2UseSpot: boolean;               // Use Spot instances for cost savings
  ec2SpotMaxPrice?: string;          // Max price for Spot (e.g., '0.05')
  ec2RootVolumeSize: number;         // Root volume size in GB
  ec2EnableDockerSocket: boolean;    // Mount Docker socket for PTC support

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

  // PTC (Programmatic Tool Calling) Configuration
  enablePtc: boolean;                // Enable PTC feature (requires EC2 launch type)
  ptcSandboxImage: string;           // Docker image for PTC sandbox
  ptcSessionTimeout: number;         // Session timeout in seconds
  ptcExecutionTimeout: number;       // Code execution timeout in seconds
  ptcMemoryLimit: string;            // Container memory limit (e.g., '256m')

  // Bedrock Concurrency Settings
  bedrockThreadPoolSize: number;
  bedrockSemaphoreSize: number;

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

// Environment configurations without runtime settings (platform and launchType are set at deployment time)
type EnvironmentConfigWithoutRuntime = Omit<EnvironmentConfig, 'platform' | 'launchType'>;

export const environments: { [key: string]: EnvironmentConfigWithoutRuntime } = {
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

    // EC2 Launch Type (used when launchType is 'ec2')
    ec2InstanceType: 't3.medium',  // Will be overridden based on platform
    ec2UseSpot: true,              // Use Spot for dev to save cost
    ec2RootVolumeSize: 30,         // 30GB root volume
    ec2EnableDockerSocket: true,   // Enable Docker socket for PTC

    // Container
    containerPort: 8000,
    healthCheckPath: '/health',
    healthCheckInterval: 30,
    healthCheckTimeout: 10,
    healthCheckHealthyThreshold: 2,
    healthCheckUnhealthyThreshold: 5,

    // Application
    requireApiKey: true,
    rateLimitEnabled: true,
    rateLimitRequests: 100,
    rateLimitWindow: 60,
    enableMetrics: true,

    // PTC (Programmatic Tool Calling)
    enablePtc: false,                     // Disabled by default, enabled when using EC2
    ptcSandboxImage: 'python:3.11-slim',
    ptcSessionTimeout: 270,               // 4.5 minutes
    ptcExecutionTimeout: 60,
    ptcMemoryLimit: '256m',

    // Bedrock Concurrency
    bedrockThreadPoolSize: 15,
    bedrockSemaphoreSize: 15,

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
    ecsCpu: 1536,
    ecsMemory: 3072,
    ecsMinCapacity: 2,
    ecsMaxCapacity: 10,
    ecsTargetCpuUtilization: 70,

    // EC2 Launch Type (used when launchType is 'ec2')
    ec2InstanceType: 't3.xlarge',   // Will be overridden based on platform
    ec2UseSpot: false,             // Use On-Demand for prod stability
    ec2RootVolumeSize: 50,         // 50GB root volume
    ec2EnableDockerSocket: true,   // Enable Docker socket for PTC

    // Container
    containerPort: 8000,
    healthCheckPath: '/health',
    healthCheckInterval: 30,
    healthCheckTimeout: 10,
    healthCheckHealthyThreshold: 2,
    healthCheckUnhealthyThreshold: 5,

    // Application
    requireApiKey: true,
    rateLimitEnabled: true,
    rateLimitRequests: 1000,
    rateLimitWindow: 60,
    enableMetrics: true,

    // PTC (Programmatic Tool Calling)
    enablePtc: false,                     // Disabled by default, enabled when using EC2
    ptcSandboxImage: 'python:3.11-slim',
    ptcSessionTimeout: 270,               // 4.5 minutes
    ptcExecutionTimeout: 60,
    ptcMemoryLimit: '256m',

    // Bedrock Concurrency
    bedrockThreadPoolSize: 30,
    bedrockSemaphoreSize: 30,

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

// Helper function to get EC2 instance type based on platform
function getEc2InstanceType(baseType: string, platform: 'arm64' | 'amd64'): string {
  // Map x86 instance types to ARM equivalents
  const armMapping: { [key: string]: string } = {
    't3.micro': 't4g.micro',
    't3.small': 't4g.small',
    't3.medium': 't4g.medium',
    't3.large': 't4g.large',
    't3.xlarge': 't4g.xlarge',
    't3.2xlarge': 't4g.2xlarge',
    'm5.large': 'm6g.large',
    'm5.xlarge': 'm6g.xlarge',
    'm5.2xlarge': 'm6g.2xlarge',
    'c5.large': 'c6g.large',
    'c5.xlarge': 'c6g.xlarge',
    'c5.2xlarge': 'c6g.2xlarge',
    'r5.large': 'r6g.large',
    'r5.xlarge': 'r6g.xlarge',
  };

  if (platform === 'arm64' && armMapping[baseType]) {
    return armMapping[baseType];
  }
  return baseType;
}

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

  // Get launch type from environment variable (set by deploy script)
  // Default to 'fargate' if not specified
  const launchType = (process.env.CDK_LAUNCH_TYPE as 'fargate' | 'ec2') || 'fargate';
  if (!['fargate', 'ec2'].includes(launchType)) {
    throw new Error(
      `Launch type must be 'fargate' or 'ec2'. Got: ${launchType}`
    );
  }

  // Adjust EC2 instance type based on platform
  const ec2InstanceType = getEc2InstanceType(config.ec2InstanceType, platform);

  // Enable PTC automatically when using EC2 launch type with Docker socket
  const enablePtc = launchType === 'ec2' && config.ec2EnableDockerSocket;

  return {
    ...config,
    platform,
    launchType,
    ec2InstanceType,
    enablePtc,
  };
}
