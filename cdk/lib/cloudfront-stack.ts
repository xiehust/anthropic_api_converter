import * as cdk from 'aws-cdk-lib';
import * as cloudfront from 'aws-cdk-lib/aws-cloudfront';
import * as origins from 'aws-cdk-lib/aws-cloudfront-origins';
import * as elbv2 from 'aws-cdk-lib/aws-elasticloadbalancingv2';
import * as wafv2 from 'aws-cdk-lib/aws-wafv2';
import { Construct } from 'constructs';
import { EnvironmentConfig } from '../config/config';

export interface CloudFrontStackProps extends cdk.StackProps {
  config: EnvironmentConfig;
  alb: elbv2.ApplicationLoadBalancer;
}

export class CloudFrontStack extends cdk.Stack {
  public readonly distribution: cloudfront.Distribution;

  constructor(scope: Construct, id: string, props: CloudFrontStackProps) {
    super(scope, id, props);

    const { config, alb } = props;

    if (!config.enableCloudFront) {
      return;
    }

    // Create Cache Policy with proper header forwarding
    const cachePolicy = new cloudfront.CachePolicy(this, 'CachePolicy', {
      cachePolicyName: `anthropic-proxy-${config.environmentName}-cache`,
      comment: 'Cache policy for Anthropic proxy with custom headers',
      // Disable caching for API requests
      defaultTtl: cdk.Duration.seconds(0),
      minTtl: cdk.Duration.seconds(0),
      maxTtl: cdk.Duration.seconds(1),
      cookieBehavior: cloudfront.CacheCookieBehavior.none(),
      headerBehavior: cloudfront.CacheHeaderBehavior.allowList(
        // Required headers for Anthropic API
        'x-api-key',
        'anthropic-version',
        'content-type',
        'accept',
        'anthropic-beta',
        // Standard headers
        'authorization',
        'user-agent',
        'origin',
        'referer'
      ),
      queryStringBehavior: cloudfront.CacheQueryStringBehavior.all(),
      enableAcceptEncodingGzip: true,
      enableAcceptEncodingBrotli: true,
    });

    // Create Origin Request Policy to forward all necessary headers
    const originRequestPolicy = new cloudfront.OriginRequestPolicy(this, 'OriginRequestPolicy', {
      originRequestPolicyName: `anthropic-proxy-${config.environmentName}-origin`,
      comment: 'Forward all headers and query strings to origin',
      headerBehavior: cloudfront.OriginRequestHeaderBehavior.all(),
      queryStringBehavior: cloudfront.OriginRequestQueryStringBehavior.all(),
      cookieBehavior: cloudfront.OriginRequestCookieBehavior.none(),
    });

    // Create Response Headers Policy
    const responseHeadersPolicy = new cloudfront.ResponseHeadersPolicy(this, 'ResponseHeadersPolicy', {
      responseHeadersPolicyName: `anthropic-proxy-${config.environmentName}-response`,
      comment: 'Response headers for Anthropic proxy',
      corsBehavior: {
        accessControlAllowOrigins: ['*'],
        accessControlAllowHeaders: ['*'],
        accessControlAllowMethods: ['GET', 'HEAD', 'POST', 'PUT', 'PATCH', 'DELETE', 'OPTIONS'],
        accessControlAllowCredentials: false,
        accessControlMaxAge: cdk.Duration.seconds(600),
        originOverride: false,
      },
      securityHeadersBehavior: {
        contentTypeOptions: { override: true },
        frameOptions: { frameOption: cloudfront.HeadersFrameOption.DENY, override: true },
        referrerPolicy: {
          referrerPolicy: cloudfront.HeadersReferrerPolicy.STRICT_ORIGIN_WHEN_CROSS_ORIGIN,
          override: true,
        },
        strictTransportSecurity: {
          accessControlMaxAge: cdk.Duration.seconds(63072000),
          includeSubdomains: true,
          override: true,
        },
        xssProtection: { protection: true, modeBlock: true, override: true },
      },
    });

    // Create CloudFront Distribution
    this.distribution = new cloudfront.Distribution(this, 'Distribution', {
      distributionName: `anthropic-proxy-${config.environmentName}`,
      comment: `CloudFront distribution for Anthropic proxy ${config.environmentName}`,
      enabled: true,
      httpVersion: cloudfront.HttpVersion.HTTP2_AND_3,
      priceClass: cloudfront.PriceClass[config.cloudfrontPriceClass as keyof typeof cloudfront.PriceClass],

      defaultBehavior: {
        origin: new origins.LoadBalancerV2Origin(alb, {
          protocolPolicy: cloudfront.OriginProtocolPolicy.HTTP_ONLY,
          httpPort: 80,
          // Set maximum timeout values for long-running streaming requests
          readTimeout: cdk.Duration.seconds(60), // Max value for CloudFront
          keepaliveTimeout: cdk.Duration.seconds(60), // Max value
          connectionAttempts: 3,
          connectionTimeout: cdk.Duration.seconds(10),
          customHeaders: {
            'X-Forwarded-Proto': 'https',
          },
        }),

        // Allow all HTTP methods
        allowedMethods: cloudfront.AllowedMethods.ALLOW_ALL,
        cachedMethods: cloudfront.CachedMethods.CACHE_GET_HEAD_OPTIONS,

        // Apply policies
        cachePolicy,
        originRequestPolicy,
        responseHeadersPolicy,

        // Viewer protocol policy
        viewerProtocolPolicy: cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,

        // Compress responses
        compress: true,
      },

      // Enable logging for production
      enableLogging: config.environmentName === 'prod',
      logIncludesCookies: false,

      // Error responses
      errorResponses: [
        {
          httpStatus: 500,
          ttl: cdk.Duration.seconds(0),
        },
        {
          httpStatus: 502,
          ttl: cdk.Duration.seconds(0),
        },
        {
          httpStatus: 503,
          ttl: cdk.Duration.seconds(0),
        },
        {
          httpStatus: 504,
          ttl: cdk.Duration.seconds(0),
        },
      ],
    });

    // Create WAF Web ACL for production
    if (config.environmentName === 'prod') {
      const webAcl = new wafv2.CfnWebACL(this, 'WebACL', {
        scope: 'CLOUDFRONT',
        defaultAction: { allow: {} },
        name: `anthropic-proxy-${config.environmentName}-waf`,
        visibilityConfig: {
          cloudWatchMetricsEnabled: true,
          metricName: `anthropic-proxy-${config.environmentName}-waf-metric`,
          sampledRequestsEnabled: true,
        },
        rules: [
          // Rate limiting rule
          {
            name: 'RateLimit',
            priority: 1,
            action: {
              block: {
                customResponse: {
                  responseCode: 429,
                },
              },
            },
            statement: {
              rateBasedStatement: {
                limit: 2000, // 2000 requests per 5 minutes per IP
                aggregateKeyType: 'IP',
              },
            },
            visibilityConfig: {
              cloudWatchMetricsEnabled: true,
              metricName: 'RateLimitRule',
              sampledRequestsEnabled: true,
            },
          },
          // AWS Managed Rules - Common Rule Set
          {
            name: 'AWSManagedRulesCommonRuleSet',
            priority: 2,
            overrideAction: { none: {} },
            statement: {
              managedRuleGroupStatement: {
                vendorName: 'AWS',
                name: 'AWSManagedRulesCommonRuleSet',
              },
            },
            visibilityConfig: {
              cloudWatchMetricsEnabled: true,
              metricName: 'AWSManagedRulesCommonRuleSetMetric',
              sampledRequestsEnabled: true,
            },
          },
          // AWS Managed Rules - Known Bad Inputs
          {
            name: 'AWSManagedRulesKnownBadInputsRuleSet',
            priority: 3,
            overrideAction: { none: {} },
            statement: {
              managedRuleGroupStatement: {
                vendorName: 'AWS',
                name: 'AWSManagedRulesKnownBadInputsRuleSet',
              },
            },
            visibilityConfig: {
              cloudWatchMetricsEnabled: true,
              metricName: 'AWSManagedRulesKnownBadInputsRuleSetMetric',
              sampledRequestsEnabled: true,
            },
          },
        ],
      });

      // Associate WAF with CloudFront
      new wafv2.CfnWebACLAssociation(this, 'WebACLAssociation', {
        resourceArn: this.distribution.distributionArn,
        webAclArn: webAcl.attrArn,
      });
    }

    // Apply tags
    Object.entries(config.tags).forEach(([key, value]) => {
      cdk.Tags.of(this.distribution).add(key, value);
    });

    // Outputs
    new cdk.CfnOutput(this, 'DistributionId', {
      value: this.distribution.distributionId,
      description: 'CloudFront Distribution ID',
      exportName: `${config.environmentName}-distribution-id`,
    });

    new cdk.CfnOutput(this, 'DistributionDomainName', {
      value: this.distribution.distributionDomainName,
      description: 'CloudFront Distribution Domain Name',
      exportName: `${config.environmentName}-distribution-domain`,
    });

    new cdk.CfnOutput(this, 'DistributionURL', {
      value: `https://${this.distribution.distributionDomainName}`,
      description: 'CloudFront Distribution URL',
      exportName: `${config.environmentName}-distribution-url`,
    });
  }
}
