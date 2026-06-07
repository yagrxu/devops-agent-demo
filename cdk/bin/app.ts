#!/usr/bin/env node
import * as cdk from 'aws-cdk-lib';
import { DemoInfraStack } from '../lib/demo-infra-stack';

const app = new cdk.App();

new DemoInfraStack(app, 'DevOpsAgentDemoStack', {
  env: {
    account: process.env.CDK_DEFAULT_ACCOUNT,
    region: process.env.CDK_DEFAULT_REGION || process.env.AWS_REGION || 'ap-southeast-1',
  },
  description: 'DevOps Agent Skills Demo - Redis + Aurora + MSK Serverless with CloudWatch Alarms',
});
