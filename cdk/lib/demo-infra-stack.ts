import * as cdk from 'aws-cdk-lib';
import * as ec2 from 'aws-cdk-lib/aws-ec2';
import * as elasticache from 'aws-cdk-lib/aws-elasticache';
import * as rds from 'aws-cdk-lib/aws-rds';
import * as msk from 'aws-cdk-lib/aws-msk';
import * as cloudwatch from 'aws-cdk-lib/aws-cloudwatch';
import * as sns from 'aws-cdk-lib/aws-sns';
import * as iam from 'aws-cdk-lib/aws-iam';
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

    // --- SNS Topic for Alarms ---
    const alarmTopic = new sns.Topic(this, 'DemoAlarmTopic', {
      topicName: 'devops-agent-demo-alarms',
      displayName: 'DevOps Agent Demo Alarms',
    });

    // --- ElastiCache Redis (Serverless) ---
    const redisSubnetGroup = new elasticache.CfnSubnetGroup(this, 'RedisSubnetGroup', {
      description: 'Demo Redis subnet group',
      subnetIds: vpc.privateSubnets.map(s => s.subnetId),
      cacheSubnetGroupName: 'devops-agent-demo-redis-sg',
    });

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
        version: rds.AuroraPostgresEngineVersion.VER_15_4,
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

    // --- CloudWatch Alarms ---

    // Redis: Memory usage high (simulated via ElastiCache metrics)
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
      threshold: 4 * 1024 * 1024 * 1024, // 4GB of 5GB limit
      evaluationPeriods: 2,
      comparisonOperator: cloudwatch.ComparisonOperator.GREATER_THAN_THRESHOLD,
      treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
    });
    redisMemoryAlarm.addAlarmAction({ bind: () => ({ alarmActionArn: alarmTopic.topicArn }) });

    // Redis: Evictions alarm
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

    // RDS: Connection count high
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
      threshold: 50, // Low threshold for demo (serverless has limited connections)
      evaluationPeriods: 2,
      comparisonOperator: cloudwatch.ComparisonOperator.GREATER_THAN_THRESHOLD,
      treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
    });
    rdsConnectionAlarm.addAlarmAction({ bind: () => ({ alarmActionArn: alarmTopic.topicArn }) });

    // RDS: High latency (using ReadLatency as proxy)
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
      threshold: 0.02, // 20ms (high for Aurora)
      evaluationPeriods: 2,
      comparisonOperator: cloudwatch.ComparisonOperator.GREATER_THAN_THRESHOLD,
      treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
    });
    rdsLatencyAlarm.addAlarmAction({ bind: () => ({ alarmActionArn: alarmTopic.topicArn }) });

    // Custom metric alarm for checkout p99 (we'll push this metric from the injection script)
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
      threshold: 200, // 200ms SLA
      evaluationPeriods: 2,
      comparisonOperator: cloudwatch.ComparisonOperator.GREATER_THAN_THRESHOLD,
      treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
    });
    checkoutP99Alarm.addAlarmAction({ bind: () => ({ alarmActionArn: alarmTopic.topicArn }) });

    // Custom metric alarm for MSK consumer lag
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

    // --- Bastion Host (for running injection scripts) ---
    const bastion = new ec2.BastionHostLinux(this, 'DemoBastion', {
      vpc,
      instanceType: ec2.InstanceType.of(ec2.InstanceClass.T3, ec2.InstanceSize.MICRO),
      subnetSelection: { subnetType: ec2.SubnetType.PRIVATE_WITH_EGRESS },
      securityGroup: internalSg,
    });
    bastion.instance.addToRolePolicy(new iam.PolicyStatement({
      actions: ['cloudwatch:PutMetricData'],
      resources: ['*'],
    }));
    bastion.instance.addToRolePolicy(new iam.PolicyStatement({
      actions: ['cloudwatch:SetAlarmState'],
      resources: ['*'],
    }));

    // --- Outputs ---
    new cdk.CfnOutput(this, 'VpcId', { value: vpc.vpcId });
    new cdk.CfnOutput(this, 'RedisEndpoint', {
      value: redis.attrEndpointAddress ?? 'pending',
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
      value: msk_cluster_arn(mskCluster),
      description: 'MSK Serverless cluster ARN',
    });
    new cdk.CfnOutput(this, 'AlarmTopicArn', {
      value: alarmTopic.topicArn,
      description: 'SNS topic for alarms',
    });
    new cdk.CfnOutput(this, 'BastionInstanceId', {
      value: bastion.instanceId,
      description: 'Bastion host for SSM Session Manager',
    });
  }
}

function msk_cluster_arn(cluster: msk.CfnServerlessCluster): string {
  return cluster.attrArn;
}
