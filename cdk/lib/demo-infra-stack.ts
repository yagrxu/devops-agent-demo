import * as cdk from 'aws-cdk-lib';
import * as ec2 from 'aws-cdk-lib/aws-ec2';
import * as ecs from 'aws-cdk-lib/aws-ecs';
import * as ecr_assets from 'aws-cdk-lib/aws-ecr-assets';
import * as elbv2 from 'aws-cdk-lib/aws-elasticloadbalancingv2';
import * as cloudfront from 'aws-cdk-lib/aws-cloudfront';
import * as origins from 'aws-cdk-lib/aws-cloudfront-origins';
import * as elasticache from 'aws-cdk-lib/aws-elasticache';
import * as rds from 'aws-cdk-lib/aws-rds';
import * as msk from 'aws-cdk-lib/aws-msk';
import * as cloudwatch from 'aws-cdk-lib/aws-cloudwatch';
import * as sns from 'aws-cdk-lib/aws-sns';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as logs from 'aws-cdk-lib/aws-logs';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as cr from 'aws-cdk-lib/custom-resources';
import * as path from 'path';
import { Construct } from 'constructs';

export class DemoInfraStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props?: cdk.StackProps) {
    super(scope, id, props);

    // --- VPC ---
    const vpc = new ec2.Vpc(this, 'DemoVpc', {
      maxAzs: 2,
      natGateways: 1,
      subnetConfiguration: [
        { cidrMask: 24, name: 'Public', subnetType: ec2.SubnetType.PUBLIC },
        { cidrMask: 24, name: 'Private', subnetType: ec2.SubnetType.PRIVATE_WITH_EGRESS },
      ],
    });

    // Security group for internal access
    const internalSg = new ec2.SecurityGroup(this, 'InternalSg', {
      vpc,
      description: 'Allow internal access between demo resources',
      allowAllOutbound: true,
    });
    internalSg.addIngressRule(internalSg, ec2.Port.allTraffic(), 'Self-referencing');

    // ALB security group — only allow CloudFront IP ranges
    const albSg = new ec2.SecurityGroup(this, 'AlbSg', {
      vpc,
      description: 'ALB security group - CloudFront only',
      allowAllOutbound: true,
    });
    // Lookup the AWS-managed CloudFront origin-facing prefix list dynamically
    const cfPrefixList = ec2.PrefixList.fromLookup(this, 'CloudFrontPrefixList', {
      prefixListName: 'com.amazonaws.global.cloudfront.origin-facing',
    });
    albSg.addIngressRule(
      ec2.Peer.prefixList(cfPrefixList.prefixListId),
      ec2.Port.tcp(80),
      'HTTP from CloudFront only'
    );
    internalSg.addIngressRule(albSg, ec2.Port.tcp(8080), 'From ALB to ECS');

    // Secret header value to verify requests come from our CloudFront distribution
    const cfOriginSecret = 'quickmart-demo-cf-origin-2026';

    // --- SNS Topic for Alarms ---
    const alarmTopic = new sns.Topic(this, 'DemoAlarmTopic', {
      topicName: 'devops-agent-demo-alarms',
      displayName: 'DevOps Agent Demo Alarms',
    });

    // --- ElastiCache Redis (Serverless) ---
    const redis = new elasticache.CfnServerlessCache(this, 'DemoRedis', {
      serverlessCacheName: 'quickmart-demo-redis',
      engine: 'redis',
      majorEngineVersion: '7',
      securityGroupIds: [internalSg.securityGroupId],
      subnetIds: vpc.privateSubnets.map(s => s.subnetId),
      cacheUsageLimits: {
        dataStorage: { maximum: 5, unit: 'GB' },
        ecpuPerSecond: { maximum: 10000 },
      },
    });

    // --- Aurora Serverless v2 (PostgreSQL) ---
    const dbCluster = new rds.DatabaseCluster(this, 'DemoAurora', {
      engine: rds.DatabaseClusterEngine.auroraPostgres({
        version: rds.AuroraPostgresEngineVersion.VER_16_8,
      }),
      serverlessV2MinCapacity: 0.5,
      serverlessV2MaxCapacity: 4,
      writer: rds.ClusterInstance.serverlessV2('writer', {
        publiclyAccessible: false,
      }),
      vpc,
      vpcSubnets: { subnetType: ec2.SubnetType.PRIVATE_WITH_EGRESS },
      securityGroups: [internalSg],
      defaultDatabaseName: 'quickmart',
      credentials: rds.Credentials.fromGeneratedSecret('demoadmin', {
        secretName: 'devops-agent-demo/aurora-credentials',
      }),
      removalPolicy: cdk.RemovalPolicy.DESTROY,
      deletionProtection: false,
      storageEncrypted: true,
    });

    // --- MSK Serverless ---
    const mskCluster = new msk.CfnServerlessCluster(this, 'DemoMsk', {
      clusterName: 'quickmart-demo-msk',
      clientAuthentication: {
        sasl: {
          iam: { enabled: true },
        },
      },
      vpcConfigs: [{
        subnetIds: vpc.privateSubnets.map(s => s.subnetId),
        securityGroups: [internalSg.securityGroupId],
      }],
    });

    // --- ECS Cluster + Fargate Service ---
    const cluster = new ecs.Cluster(this, 'DemoCluster', {
      vpc,
      clusterName: 'quickmart-demo',
    });

    const taskDef = new ecs.FargateTaskDefinition(this, 'CheckoutTaskDef', {
      memoryLimitMiB: 1024,
      cpu: 512,
    });

    // Grant task role access to secrets + CloudWatch + MSK
    taskDef.taskRole.addToPrincipalPolicy(new iam.PolicyStatement({
      actions: ['secretsmanager:GetSecretValue'],
      resources: [dbCluster.secret!.secretArn],
    }));
    taskDef.taskRole.addToPrincipalPolicy(new iam.PolicyStatement({
      actions: ['cloudwatch:PutMetricData'],
      resources: ['*'],
    }));
    taskDef.taskRole.addToPrincipalPolicy(new iam.PolicyStatement({
      actions: ['kafka-cluster:*', 'kafka:*'],
      resources: ['*'],
    }));

    const appImage = new ecr_assets.DockerImageAsset(this, 'CheckoutImage', {
      directory: path.join(__dirname, '../../app'),
    });

    const container = taskDef.addContainer('checkout-svc', {
      image: ecs.ContainerImage.fromDockerImageAsset(appImage),
      logging: ecs.LogDrivers.awsLogs({
        streamPrefix: 'checkout-svc',
        logRetention: logs.RetentionDays.THREE_DAYS,
      }),
      environment: {
        REDIS_HOST: redis.attrEndpointAddress,
        REDIS_PORT: redis.attrEndpointPort,
        REDIS_SSL: 'true',
        DB_HOST: dbCluster.clusterEndpoint.hostname,
        DB_PORT: dbCluster.clusterEndpoint.port.toString(),
        DB_NAME: 'quickmart',
        DB_SECRET_ARN: dbCluster.secret!.secretArn,
        MSK_BOOTSTRAP: '',  // Will need bootstrap endpoint after cluster creation
        LOCK_TTL_SECONDS: '30',  // THE BUG: should be 5
        AWS_REGION: cdk.Stack.of(this).region,
      },
      portMappings: [{ containerPort: 8080 }],
    });

    const service = new ecs.FargateService(this, 'CheckoutService', {
      cluster,
      taskDefinition: taskDef,
      desiredCount: 2,
      securityGroups: [internalSg],
      vpcSubnets: { subnetType: ec2.SubnetType.PRIVATE_WITH_EGRESS },
      assignPublicIp: false,
    });

    // --- ALB ---
    const alb = new elbv2.ApplicationLoadBalancer(this, 'DemoAlb', {
      vpc,
      internetFacing: true,
      securityGroup: albSg,
    });

    const listener = alb.addListener('HttpListener', {
      port: 80,
      protocol: elbv2.ApplicationProtocol.HTTP,
      defaultAction: elbv2.ListenerAction.fixedResponse(403, {
        contentType: 'text/plain',
        messageBody: 'Forbidden - direct ALB access not allowed',
      }),
    });

    // Only forward traffic that includes the secret header from CloudFront
    listener.addTargets('CheckoutTarget', {
      port: 8080,
      targets: [service],
      priority: 1,
      conditions: [
        elbv2.ListenerCondition.httpHeader('X-Origin-Verify', [cfOriginSecret]),
      ],
      healthCheck: {
        path: '/health',
        interval: cdk.Duration.seconds(30),
        healthyThresholdCount: 2,
        unhealthyThresholdCount: 3,
      },
    });

    // --- CloudFront ---
    const distribution = new cloudfront.Distribution(this, 'DemoDistribution', {
      defaultBehavior: {
        origin: new origins.LoadBalancerV2Origin(alb, {
          protocolPolicy: cloudfront.OriginProtocolPolicy.HTTP_ONLY,
          customHeaders: {
            'X-Origin-Verify': cfOriginSecret,
          },
        }),
        viewerProtocolPolicy: cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
        allowedMethods: cloudfront.AllowedMethods.ALLOW_ALL,
        cachePolicy: cloudfront.CachePolicy.CACHING_DISABLED,
        originRequestPolicy: cloudfront.OriginRequestPolicy.ALL_VIEWER,
      },
    });

    // --- CloudWatch Alarms ---

    const redisMemoryAlarm = new cloudwatch.Alarm(this, 'RedisMemoryAlarm', {
      alarmName: 'quickmart-demo-redis-memory-high',
      alarmDescription: 'Redis memory usage exceeds 80% - potential eviction pressure',
      metric: new cloudwatch.Metric({
        namespace: 'AWS/ElastiCache',
        metricName: 'BytesUsedForCache',
        dimensionsMap: { CacheClusterId: 'quickmart-demo-redis' },
        statistic: 'Average',
        period: cdk.Duration.minutes(1),
      }),
      threshold: 4 * 1024 * 1024 * 1024,
      evaluationPeriods: 2,
      comparisonOperator: cloudwatch.ComparisonOperator.GREATER_THAN_THRESHOLD,
      treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
    });
    redisMemoryAlarm.addAlarmAction({ bind: () => ({ alarmActionArn: alarmTopic.topicArn }) });

    const redisEvictionsAlarm = new cloudwatch.Alarm(this, 'RedisEvictionsAlarm', {
      alarmName: 'quickmart-demo-redis-evictions-high',
      alarmDescription: 'Redis evictions exceeding threshold - keyspace pressure',
      metric: new cloudwatch.Metric({
        namespace: 'AWS/ElastiCache',
        metricName: 'Evictions',
        dimensionsMap: { CacheClusterId: 'quickmart-demo-redis' },
        statistic: 'Sum',
        period: cdk.Duration.minutes(1),
      }),
      threshold: 100,
      evaluationPeriods: 2,
      comparisonOperator: cloudwatch.ComparisonOperator.GREATER_THAN_THRESHOLD,
      treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
    });
    redisEvictionsAlarm.addAlarmAction({ bind: () => ({ alarmActionArn: alarmTopic.topicArn }) });

    const rdsConnectionAlarm = new cloudwatch.Alarm(this, 'RdsConnectionAlarm', {
      alarmName: 'quickmart-demo-rds-connections-high',
      alarmDescription: 'Aurora connection count approaching max_connections',
      metric: new cloudwatch.Metric({
        namespace: 'AWS/RDS',
        metricName: 'DatabaseConnections',
        dimensionsMap: { DBClusterIdentifier: dbCluster.clusterIdentifier },
        statistic: 'Maximum',
        period: cdk.Duration.minutes(1),
      }),
      threshold: 50,
      evaluationPeriods: 2,
      comparisonOperator: cloudwatch.ComparisonOperator.GREATER_THAN_THRESHOLD,
      treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
    });
    rdsConnectionAlarm.addAlarmAction({ bind: () => ({ alarmActionArn: alarmTopic.topicArn }) });

    const rdsLatencyAlarm = new cloudwatch.Alarm(this, 'RdsLatencyAlarm', {
      alarmName: 'quickmart-demo-rds-latency-high',
      alarmDescription: 'Aurora read latency exceeding SLA - checkout transaction degraded',
      metric: new cloudwatch.Metric({
        namespace: 'AWS/RDS',
        metricName: 'ReadLatency',
        dimensionsMap: { DBClusterIdentifier: dbCluster.clusterIdentifier },
        statistic: 'Average',
        period: cdk.Duration.minutes(1),
      }),
      threshold: 0.02,
      evaluationPeriods: 2,
      comparisonOperator: cloudwatch.ComparisonOperator.GREATER_THAN_THRESHOLD,
      treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
    });
    rdsLatencyAlarm.addAlarmAction({ bind: () => ({ alarmActionArn: alarmTopic.topicArn }) });

    const checkoutP99Alarm = new cloudwatch.Alarm(this, 'CheckoutP99Alarm', {
      alarmName: 'quickmart-demo-checkout-p99-high',
      alarmDescription: 'Checkout transaction p99 latency exceeds 200ms SLA',
      metric: new cloudwatch.Metric({
        namespace: 'QuickMart/Application',
        metricName: 'CheckoutP99Latency',
        dimensionsMap: { Service: 'checkout-svc', Environment: 'demo' },
        statistic: 'Maximum',
        period: cdk.Duration.minutes(1),
      }),
      threshold: 200,
      evaluationPeriods: 2,
      comparisonOperator: cloudwatch.ComparisonOperator.GREATER_THAN_THRESHOLD,
      treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
    });
    checkoutP99Alarm.addAlarmAction({ bind: () => ({ alarmActionArn: alarmTopic.topicArn }) });

    const mskLagAlarm = new cloudwatch.Alarm(this, 'MskLagAlarm', {
      alarmName: 'quickmart-demo-msk-consumer-lag-high',
      alarmDescription: 'MSK consumer lag on order.placed exceeds 30s SLA',
      metric: new cloudwatch.Metric({
        namespace: 'QuickMart/Messaging',
        metricName: 'ConsumerLagSeconds',
        dimensionsMap: { Topic: 'order.placed', ConsumerGroup: 'reconciler-cg' },
        statistic: 'Maximum',
        period: cdk.Duration.minutes(1),
      }),
      threshold: 30,
      evaluationPeriods: 2,
      comparisonOperator: cloudwatch.ComparisonOperator.GREATER_THAN_THRESHOLD,
      treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
    });
    mskLagAlarm.addAlarmAction({ bind: () => ({ alarmActionArn: alarmTopic.topicArn }) });

    // --- DevOps Agent Space + AWS Source Association ---
    const agentSpaceName = 'quickmart-demo';

    const devopsAgentSetupFn = new lambda.Function(this, 'DevOpsAgentSetupFn', {
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: 'index.handler',
      code: lambda.Code.fromAsset(path.join(__dirname, '../lambda/devops-agent-setup')),
      timeout: cdk.Duration.minutes(5),
      logRetention: logs.RetentionDays.THREE_DAYS,
    });
    devopsAgentSetupFn.addToRolePolicy(new iam.PolicyStatement({
      actions: [
        'devops-agent:CreateAgentSpace',
        'devops-agent:DeleteAgentSpace',
        'devops-agent:GetAgentSpace',
        'devops-agent:AssociateService',
        'devops-agent:DisassociateService',
      ],
      resources: ['*'],
    }));

    const devopsAgentProvider = new cr.Provider(this, 'DevOpsAgentProvider', {
      onEventHandler: devopsAgentSetupFn,
      logRetention: logs.RetentionDays.THREE_DAYS,
    });

    const devopsAgentSpace = new cdk.CustomResource(this, 'DevOpsAgentSpace', {
      serviceToken: devopsAgentProvider.serviceToken,
      properties: {
        SpaceName: agentSpaceName,
        AccountId: cdk.Stack.of(this).account,
        Region: cdk.Stack.of(this).region,
      },
    });

    // --- Outputs ---
    new cdk.CfnOutput(this, 'DevOpsAgentSpaceId', {
      value: devopsAgentSpace.getAttString('AgentSpaceId'),
      description: 'DevOps Agent space ID',
    });
    new cdk.CfnOutput(this, 'VpcId', { value: vpc.vpcId });
    new cdk.CfnOutput(this, 'CloudFrontDomain', {
      value: distribution.distributionDomainName,
      description: 'CloudFront URL (https://<domain>/checkout)',
    });
    new cdk.CfnOutput(this, 'AlbDnsName', {
      value: alb.loadBalancerDnsName,
      description: 'ALB DNS name',
    });
    new cdk.CfnOutput(this, 'RedisEndpoint', {
      value: redis.attrEndpointAddress,
      description: 'Redis Serverless endpoint',
    });
    new cdk.CfnOutput(this, 'AuroraClusterEndpoint', {
      value: dbCluster.clusterEndpoint.hostname,
      description: 'Aurora writer endpoint',
    });
    new cdk.CfnOutput(this, 'AuroraSecretArn', {
      value: dbCluster.secret?.secretArn ?? 'N/A',
      description: 'Aurora credentials secret ARN',
    });
    new cdk.CfnOutput(this, 'MskClusterArn', {
      value: mskCluster.attrArn,
      description: 'MSK Serverless cluster ARN',
    });
    new cdk.CfnOutput(this, 'AlarmTopicArn', {
      value: alarmTopic.topicArn,
      description: 'SNS topic for alarms',
    });
    new cdk.CfnOutput(this, 'EcsClusterName', {
      value: cluster.clusterName,
      description: 'ECS cluster name',
    });
  }
}
