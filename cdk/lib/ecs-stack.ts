import * as cdk from 'aws-cdk-lib';
import * as ec2 from 'aws-cdk-lib/aws-ec2';
import * as ecs from 'aws-cdk-lib/aws-ecs';
import * as elbv2 from 'aws-cdk-lib/aws-elasticloadbalancingv2';
import * as logs from 'aws-cdk-lib/aws-logs';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as dynamodb from 'aws-cdk-lib/aws-dynamodb';
import * as secretsmanager from 'aws-cdk-lib/aws-secretsmanager';
import { Platform } from 'aws-cdk-lib/aws-ecr-assets';
import { Construct } from 'constructs';
import { EnvironmentConfig } from '../config/config';
import * as path from 'path';

export interface ECSStackProps extends cdk.StackProps {
  config: EnvironmentConfig;
  vpc: ec2.Vpc;
  albSecurityGroup: ec2.SecurityGroup;
  ecsSecurityGroup: ec2.SecurityGroup;
  apiKeysTable: dynamodb.Table;
  usageTable: dynamodb.Table;
  cacheTable: dynamodb.Table;
  modelMappingTable: dynamodb.Table;
}

export class ECSStack extends cdk.Stack {
  public readonly cluster: ecs.Cluster;
  public readonly service: ecs.FargateService;
  public readonly alb: elbv2.ApplicationLoadBalancer;
  public readonly listener: elbv2.ApplicationListener;
  public readonly taskDefinition: ecs.FargateTaskDefinition;

  constructor(scope: Construct, id: string, props: ECSStackProps) {
    super(scope, id, props);

    const { config, vpc, albSecurityGroup, ecsSecurityGroup } = props;
    const { apiKeysTable, usageTable, cacheTable, modelMappingTable } = props;

    // Create ECS Cluster
    this.cluster = new ecs.Cluster(this, 'Cluster', {
      clusterName: `anthropic-proxy-${config.environmentName}`,
      vpc,
    });

    // Enable Container Insights at CloudFormation level (avoiding deprecated L2 property)
    if (config.enableContainerInsights) {
      const cfnCluster = this.cluster.node.defaultChild as ecs.CfnCluster;
      cfnCluster.clusterSettings = [{
        name: 'containerInsights',
        value: 'enabled',
      }];
    }

    // Create ALB
    this.alb = new elbv2.ApplicationLoadBalancer(this, 'ALB', {
      loadBalancerName: `anthropic-proxy-${config.environmentName}-alb`,
      vpc,
      internetFacing: true,
      securityGroup: albSecurityGroup,
      vpcSubnets: {
        subnetType: ec2.SubnetType.PUBLIC,
      },
      deletionProtection: false,
    });

    // Create Target Group
    const targetGroup = new elbv2.ApplicationTargetGroup(this, 'TargetGroup', {
      targetGroupName: `anthropic-proxy-${config.environmentName}-tg`,
      vpc,
      port: config.containerPort,
      protocol: elbv2.ApplicationProtocol.HTTP,
      targetType: elbv2.TargetType.IP,
      healthCheck: {
        path: config.healthCheckPath,
        interval: cdk.Duration.seconds(config.healthCheckInterval),
        timeout: cdk.Duration.seconds(config.healthCheckTimeout),
        healthyThresholdCount: config.healthCheckHealthyThreshold,
        unhealthyThresholdCount: 5, // Increase from 3 to 5 to be more tolerant
        healthyHttpCodes: '200',
      },
      deregistrationDelay: cdk.Duration.seconds(30),
    });

    // Create HTTP Listener
    this.listener = this.alb.addListener('HTTPListener', {
      port: 80,
      protocol: elbv2.ApplicationProtocol.HTTP,
      defaultTargetGroups: [targetGroup],
    });

    // Create CloudWatch Log Group
    const logGroup = new logs.LogGroup(this, 'LogGroup', {
      logGroupName: `/ecs/anthropic-proxy-${config.environmentName}`,
      retention: config.logRetentionDays as logs.RetentionDays,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });

    // Generate a short random suffix to prevent role name conflicts
    // This allows multiple deployments without role name collisions
    const roleSuffix = Math.random().toString(36).substring(2, 8);

    // Create Task Execution Role
    const taskExecutionRole = new iam.Role(this, 'TaskExecutionRole', {
      roleName: `anthropic-proxy-${config.environmentName}-task-execution-${roleSuffix}`,
      assumedBy: new iam.ServicePrincipal('ecs-tasks.amazonaws.com'),
      managedPolicies: [
        iam.ManagedPolicy.fromAwsManagedPolicyName('service-role/AmazonECSTaskExecutionRolePolicy'),
      ],
    });

    // Create Task Role
    const taskRole = new iam.Role(this, 'TaskRole', {
      roleName: `anthropic-proxy-${config.environmentName}-task-${roleSuffix}`,
      assumedBy: new iam.ServicePrincipal('ecs-tasks.amazonaws.com'),
    });

    // Grant DynamoDB permissions
    apiKeysTable.grantReadWriteData(taskRole);
    usageTable.grantReadWriteData(taskRole);
    cacheTable.grantReadWriteData(taskRole);
    modelMappingTable.grantReadWriteData(taskRole);

    // Grant Bedrock permissions
    taskRole.addToPolicy(
      new iam.PolicyStatement({
        effect: iam.Effect.ALLOW,
        actions: [
          'bedrock:InvokeModel',
          'bedrock:InvokeModelWithResponseStream',
          'bedrock:ListFoundationModels',
        ],
        resources: ['*'],
      })
    );

    // Grant CloudWatch Logs permissions
    taskRole.addToPolicy(
      new iam.PolicyStatement({
        effect: iam.Effect.ALLOW,
        actions: ['logs:CreateLogGroup', 'logs:CreateLogStream', 'logs:PutLogEvents'],
        resources: [logGroup.logGroupArn],
      })
    );

    // Create Secret for Master API Key (optional - can be set via environment)
    const masterApiKeySecret = new secretsmanager.Secret(this, 'MasterAPIKeySecret', {
      secretName: `anthropic-proxy-${config.environmentName}-master-api-key`,
      description: 'Master API key for Anthropic proxy',
      generateSecretString: {
        secretStringTemplate: JSON.stringify({ username: 'master' }),
        generateStringKey: 'password',
        excludePunctuation: true,
        passwordLength: 32,
      },
    });

    // Grant read access to secret
    masterApiKeySecret.grantRead(taskRole);

    // Create Task Definition
    // Map platform string to ECS CpuArchitecture
    const cpuArchitecture = config.platform === 'arm64'
      ? ecs.CpuArchitecture.ARM64
      : ecs.CpuArchitecture.X86_64;

    this.taskDefinition = new ecs.FargateTaskDefinition(this, 'TaskDefinition', {
      family: `anthropic-proxy-${config.environmentName}`,
      cpu: config.ecsCpu,
      memoryLimitMiB: config.ecsMemory,
      executionRole: taskExecutionRole,
      taskRole: taskRole,
      runtimePlatform: {
        cpuArchitecture,
        operatingSystemFamily: ecs.OperatingSystemFamily.LINUX,
      },
    });

    // Add Container
    // Map platform string to Docker Platform
    const dockerPlatform = config.platform === 'arm64'
      ? Platform.LINUX_ARM64
      : Platform.LINUX_AMD64;

    const container = this.taskDefinition.addContainer('app', {
      containerName: 'anthropic-proxy',
      image: ecs.ContainerImage.fromAsset(path.join(__dirname, '../../'), {
        file: 'Dockerfile',
        exclude: ['cdk/cdk.out', 'cdk/node_modules', 'cdk/.git'],
        platform: dockerPlatform,
      }),
      logging: ecs.LogDriver.awsLogs({
        streamPrefix: 'anthropic-proxy',
        logGroup,
      }),
      environment: {
        // AWS Configuration
        AWS_REGION: config.region,
        AWS_DEFAULT_REGION: config.region,

        // Environment
        ENVIRONMENT: config.environmentName === 'prod' ? 'production' : 'development',
        LOG_LEVEL: config.environmentName === 'prod' ? 'INFO' : 'DEBUG',

        // DynamoDB Tables
        DYNAMODB_API_KEYS_TABLE: apiKeysTable.tableName,
        DYNAMODB_USAGE_TABLE: usageTable.tableName,
        DYNAMODB_CACHE_TABLE: cacheTable.tableName,
        DYNAMODB_MODEL_MAPPING_TABLE: modelMappingTable.tableName,

        // Authentication
        API_KEY_HEADER: 'x-api-key',
        REQUIRE_API_KEY: config.requireApiKey.toString(),

        // Rate Limiting
        RATE_LIMIT_ENABLED: config.rateLimitEnabled.toString(),
        RATE_LIMIT_REQUESTS: config.rateLimitRequests.toString(),
        RATE_LIMIT_WINDOW: config.rateLimitWindow.toString(),

        // Features
        ENABLE_TOOL_USE: 'True',
        ENABLE_EXTENDED_THINKING: 'True',
        ENABLE_DOCUMENT_SUPPORT: 'True',
        PROMPT_CACHING_ENABLED: 'True',
        FINE_GRAINED_TOOL_STREAMING_ENABLED: 'True',
        INTERLEAVED_THINKING_ENABLED: 'True',

        // Metrics
        ENABLE_METRICS: config.enableMetrics.toString(),

        // Streaming
        STREAMING_TIMEOUT: '300',
      },
      secrets: {
        // Master API key from Secrets Manager
        MASTER_API_KEY: ecs.Secret.fromSecretsManager(masterApiKeySecret, 'password'),
      },
      portMappings: [
        {
          containerPort: config.containerPort,
          protocol: ecs.Protocol.TCP,
        },
      ],
      healthCheck: {
        command: [
          'CMD-SHELL',
          `curl -f http://localhost:${config.containerPort}/health || exit 1`,
        ],
        interval: cdk.Duration.seconds(30),
        timeout: cdk.Duration.seconds(5),
        retries: 3,
        startPeriod: cdk.Duration.seconds(90),
      },
    });

    // Create Fargate Service
    this.service = new ecs.FargateService(this, 'Service', {
      serviceName: `anthropic-proxy-${config.environmentName}`,
      cluster: this.cluster,
      taskDefinition: this.taskDefinition,
      desiredCount: config.ecsDesiredCount,
      assignPublicIp: false,
      securityGroups: [ecsSecurityGroup],
      vpcSubnets: {
        subnetType: ec2.SubnetType.PRIVATE_WITH_EGRESS,
      },
      healthCheckGracePeriod: cdk.Duration.seconds(180),
      circuitBreaker: {
        rollback: true,
      },
      enableExecuteCommand: config.environmentName !== 'prod',
    });

    // Attach to Target Group
    this.service.attachToApplicationTargetGroup(targetGroup);

    // Auto Scaling
    const scaling = this.service.autoScaleTaskCount({
      minCapacity: config.ecsMinCapacity,
      maxCapacity: config.ecsMaxCapacity,
    });

    // CPU-based auto scaling
    scaling.scaleOnCpuUtilization('CpuScaling', {
      targetUtilizationPercent: config.ecsTargetCpuUtilization,
      scaleInCooldown: cdk.Duration.seconds(60),
      scaleOutCooldown: cdk.Duration.seconds(60),
    });

    // Memory-based auto scaling
    scaling.scaleOnMemoryUtilization('MemoryScaling', {
      targetUtilizationPercent: 70,
      scaleInCooldown: cdk.Duration.seconds(60),
      scaleOutCooldown: cdk.Duration.seconds(60),
    });

    // Request count based scaling
    scaling.scaleOnRequestCount('RequestCountScaling', {
      requestsPerTarget: 1000,
      targetGroup,
      scaleInCooldown: cdk.Duration.seconds(60),
      scaleOutCooldown: cdk.Duration.seconds(60),
    });

    // Apply tags
    cdk.Tags.of(this.cluster).add('Environment', config.environmentName);
    Object.entries(config.tags).forEach(([key, value]) => {
      cdk.Tags.of(this.cluster).add(key, value);
    });

    // Outputs
    new cdk.CfnOutput(this, 'ClusterName', {
      value: this.cluster.clusterName,
      description: 'ECS Cluster Name',
      exportName: `${config.environmentName}-cluster-name`,
    });

    new cdk.CfnOutput(this, 'ServiceName', {
      value: this.service.serviceName,
      description: 'ECS Service Name',
      exportName: `${config.environmentName}-service-name`,
    });

    new cdk.CfnOutput(this, 'ALBDNSName', {
      value: this.alb.loadBalancerDnsName,
      description: 'ALB DNS Name',
      exportName: `${config.environmentName}-alb-dns`,
    });

    new cdk.CfnOutput(this, 'ALBARN', {
      value: this.alb.loadBalancerArn,
      description: 'ALB ARN',
      exportName: `${config.environmentName}-alb-arn`,
    });

    new cdk.CfnOutput(this, 'MasterAPIKeySecretName', {
      value: masterApiKeySecret.secretName,
      description: 'Master API Key Secret Name',
      exportName: `${config.environmentName}-master-api-key-secret`,
    });
  }
}
