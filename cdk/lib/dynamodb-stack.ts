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
  public readonly cacheTable: dynamodb.Table;
  public readonly modelMappingTable: dynamodb.Table;

  constructor(scope: Construct, id: string, props: DynamoDBStackProps) {
    super(scope, id, props);

    const { config } = props;
    const tablePrefix = `anthropic-proxy-${config.environmentName}`;

    // API Keys Table
    this.apiKeysTable = new dynamodb.Table(this, 'APIKeysTable', {
      tableName: `${tablePrefix}-api-keys`,
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
      pointInTimeRecovery: config.environmentName === 'prod',
      encryption: dynamodb.TableEncryption.AWS_MANAGED,
      tags: config.tags,
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
      tableName: `${tablePrefix}-usage`,
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
      pointInTimeRecovery: config.environmentName === 'prod',
      encryption: dynamodb.TableEncryption.AWS_MANAGED,
      timeToLiveAttribute: 'ttl',
      tags: config.tags,
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

    // Cache Table
    this.cacheTable = new dynamodb.Table(this, 'CacheTable', {
      tableName: `${tablePrefix}-cache`,
      partitionKey: {
        name: 'cache_key',
        type: dynamodb.AttributeType.STRING,
      },
      billingMode:
        config.dynamodbBillingMode === 'PAY_PER_REQUEST'
          ? dynamodb.BillingMode.PAY_PER_REQUEST
          : dynamodb.BillingMode.PROVISIONED,
      readCapacity: config.dynamodbReadCapacity,
      writeCapacity: config.dynamodbWriteCapacity,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
      encryption: dynamodb.TableEncryption.AWS_MANAGED,
      timeToLiveAttribute: 'ttl',
      tags: config.tags,
    });

    // Model Mapping Table
    this.modelMappingTable = new dynamodb.Table(this, 'ModelMappingTable', {
      tableName: `${tablePrefix}-model-mapping`,
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
      tags: config.tags,
    });

    // Outputs
    new cdk.CfnOutput(this, 'APIKeysTableName', {
      value: this.apiKeysTable.tableName,
      description: 'API Keys DynamoDB Table Name',
      exportName: `${config.environmentName}-api-keys-table`,
    });

    new cdk.CfnOutput(this, 'UsageTableName', {
      value: this.usageTable.tableName,
      description: 'Usage Tracking DynamoDB Table Name',
      exportName: `${config.environmentName}-usage-table`,
    });

    new cdk.CfnOutput(this, 'CacheTableName', {
      value: this.cacheTable.tableName,
      description: 'Cache DynamoDB Table Name',
      exportName: `${config.environmentName}-cache-table`,
    });

    new cdk.CfnOutput(this, 'ModelMappingTableName', {
      value: this.modelMappingTable.tableName,
      description: 'Model Mapping DynamoDB Table Name',
      exportName: `${config.environmentName}-model-mapping-table`,
    });
  }
}
