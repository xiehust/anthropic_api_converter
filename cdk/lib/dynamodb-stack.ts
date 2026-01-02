import * as cdk from 'aws-cdk-lib';
import * as dynamodb from 'aws-cdk-lib/aws-dynamodb';
import { Construct } from 'constructs';
import { EnvironmentConfig } from '../config/config';

export interface DynamoDBStackProps extends cdk.StackProps {
  config: EnvironmentConfig;
}

export class DynamoDBStack extends cdk.Stack {
  public readonly apiKeysTable: dynamodb.Table;
  public readonly usageTable: dynamodb.Table;
  public readonly modelMappingTable: dynamodb.Table;
  public readonly usageStatsTable: dynamodb.Table;
  public readonly modelPricingTable: dynamodb.Table;

  constructor(scope: Construct, id: string, props: DynamoDBStackProps) {
    super(scope, id, props);

    const { config } = props;

    // API Keys Table
    // Note: tableName is intentionally omitted to let CDK generate unique names
    // and avoid resource conflicts across deployments
    this.apiKeysTable = new dynamodb.Table(this, 'APIKeysTable', {
      partitionKey: {
        name: 'api_key',
        type: dynamodb.AttributeType.STRING,
      },
      billingMode:
        config.dynamodbBillingMode === 'PAY_PER_REQUEST'
          ? dynamodb.BillingMode.PAY_PER_REQUEST
          : dynamodb.BillingMode.PROVISIONED,
      readCapacity: config.dynamodbReadCapacity,
      writeCapacity: config.dynamodbWriteCapacity,
      removalPolicy: cdk.RemovalPolicy.RETAIN,
      pointInTimeRecoverySpecification: {
        pointInTimeRecoveryEnabled: config.environmentName === 'prod',
      },
      encryption: dynamodb.TableEncryption.AWS_MANAGED,
    });

    // GSI for user_id lookups
    this.apiKeysTable.addGlobalSecondaryIndex({
      indexName: 'user_id-index',
      partitionKey: {
        name: 'user_id',
        type: dynamodb.AttributeType.STRING,
      },
      projectionType: dynamodb.ProjectionType.ALL,
    });

    // Usage Tracking Table
    this.usageTable = new dynamodb.Table(this, 'UsageTable', {
      partitionKey: {
        name: 'api_key',
        type: dynamodb.AttributeType.STRING,
      },
      sortKey: {
        name: 'timestamp',
        type: dynamodb.AttributeType.STRING,
      },
      billingMode:
        config.dynamodbBillingMode === 'PAY_PER_REQUEST'
          ? dynamodb.BillingMode.PAY_PER_REQUEST
          : dynamodb.BillingMode.PROVISIONED,
      readCapacity: config.dynamodbReadCapacity,
      writeCapacity: config.dynamodbWriteCapacity,
      removalPolicy: cdk.RemovalPolicy.RETAIN,
      pointInTimeRecoverySpecification: {
        pointInTimeRecoveryEnabled: config.environmentName === 'prod',
      },
      encryption: dynamodb.TableEncryption.AWS_MANAGED,
      timeToLiveAttribute: 'ttl',
    });

    // GSI for request_id lookups
    this.usageTable.addGlobalSecondaryIndex({
      indexName: 'request_id-index',
      partitionKey: {
        name: 'request_id',
        type: dynamodb.AttributeType.STRING,
      },
      projectionType: dynamodb.ProjectionType.ALL,
    });

    // Model Mapping Table
    this.modelMappingTable = new dynamodb.Table(this, 'ModelMappingTable', {
      partitionKey: {
        name: 'anthropic_model_id',
        type: dynamodb.AttributeType.STRING,
      },
      billingMode:
        config.dynamodbBillingMode === 'PAY_PER_REQUEST'
          ? dynamodb.BillingMode.PAY_PER_REQUEST
          : dynamodb.BillingMode.PROVISIONED,
      readCapacity: config.dynamodbReadCapacity,
      writeCapacity: config.dynamodbWriteCapacity,
      removalPolicy: cdk.RemovalPolicy.RETAIN,
      encryption: dynamodb.TableEncryption.AWS_MANAGED,
    });

    // Usage Stats Table (aggregated usage statistics)
    // Used by admin portal for displaying aggregated token counts and budget
    this.usageStatsTable = new dynamodb.Table(this, 'UsageStatsTable', {
      partitionKey: {
        name: 'api_key',
        type: dynamodb.AttributeType.STRING,
      },
      billingMode:
        config.dynamodbBillingMode === 'PAY_PER_REQUEST'
          ? dynamodb.BillingMode.PAY_PER_REQUEST
          : dynamodb.BillingMode.PROVISIONED,
      readCapacity: config.dynamodbReadCapacity,
      writeCapacity: config.dynamodbWriteCapacity,
      removalPolicy: cdk.RemovalPolicy.RETAIN,
      pointInTimeRecoverySpecification: {
        pointInTimeRecoveryEnabled: config.environmentName === 'prod',
      },
      encryption: dynamodb.TableEncryption.AWS_MANAGED,
    });

    // Model Pricing Table (model pricing configuration)
    // Used by admin portal for cost calculation and pricing management
    this.modelPricingTable = new dynamodb.Table(this, 'ModelPricingTable', {
      partitionKey: {
        name: 'model_id',
        type: dynamodb.AttributeType.STRING,
      },
      billingMode:
        config.dynamodbBillingMode === 'PAY_PER_REQUEST'
          ? dynamodb.BillingMode.PAY_PER_REQUEST
          : dynamodb.BillingMode.PROVISIONED,
      readCapacity: config.dynamodbReadCapacity,
      writeCapacity: config.dynamodbWriteCapacity,
      removalPolicy: cdk.RemovalPolicy.RETAIN,
      encryption: dynamodb.TableEncryption.AWS_MANAGED,
    });

    // Apply tags to all tables
    Object.entries(config.tags).forEach(([key, value]) => {
      cdk.Tags.of(this.apiKeysTable).add(key, value);
      cdk.Tags.of(this.usageTable).add(key, value);
      cdk.Tags.of(this.modelMappingTable).add(key, value);
      cdk.Tags.of(this.usageStatsTable).add(key, value);
      cdk.Tags.of(this.modelPricingTable).add(key, value);
    });

    // Outputs - exportName omitted to avoid cross-stack conflicts
    new cdk.CfnOutput(this, 'APIKeysTableName', {
      value: this.apiKeysTable.tableName,
      description: 'API Keys DynamoDB Table Name',
    });

    new cdk.CfnOutput(this, 'UsageTableName', {
      value: this.usageTable.tableName,
      description: 'Usage Tracking DynamoDB Table Name',
    });

    new cdk.CfnOutput(this, 'ModelMappingTableName', {
      value: this.modelMappingTable.tableName,
      description: 'Model Mapping DynamoDB Table Name',
    });

    new cdk.CfnOutput(this, 'UsageStatsTableName', {
      value: this.usageStatsTable.tableName,
      description: 'Usage Stats DynamoDB Table Name',
    });

    new cdk.CfnOutput(this, 'ModelPricingTableName', {
      value: this.modelPricingTable.tableName,
      description: 'Model Pricing DynamoDB Table Name',
    });
  }
}
