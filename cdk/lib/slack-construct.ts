import * as cdk from 'aws-cdk-lib';
import { Construct } from 'constructs';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as sns from 'aws-cdk-lib/aws-sns';
import * as sns_subs from 'aws-cdk-lib/aws-sns-subscriptions';
import * as apigatewayv2 from 'aws-cdk-lib/aws-apigatewayv2';
import * as apigatewayv2_integrations from 'aws-cdk-lib/aws-apigatewayv2-integrations';
import * as logs from 'aws-cdk-lib/aws-logs';
import * as path from 'path';

export interface DevOpsAgentSlackProps {
  /** Project name prefix for resource naming. */
  readonly projectName: string;
  /** Existing SNS alarm topic ARN to subscribe the webhook forwarder. */
  readonly alarmTopicArn: string;
  /** Secrets Manager ARN for the webhook secret {url, hmac_secret}. */
  readonly webhookSecretArn: string;
  /** Secrets Manager ARN for the Slack secret {bot_token, signing_secret, agent_space_id, operator_role_arn}. */
  readonly slackSecretArn: string;
  /** DevOps Agent operator role ARN the worker assumes (with AgentSpaceId tag). */
  readonly operatorRoleArn: string;
  /** Secrets Manager secret name for the webhook (used as env var). */
  readonly webhookSecretName: string;
  /** Secrets Manager secret name for the Slack bot (used as env var). */
  readonly slackSecretName: string;
  /** Unique deployment suffix for the worker role name. */
  readonly deploymentId: string;
}

/**
 * Reusable CDK construct for DevOps Agent Slack integration.
 *
 * Path A (automated): SNS alarm → Webhook Lambda → HMAC-signed POST → DevOps
 *   Agent webhook → autonomous investigation.
 *
 * Path B (interactive): Slack event → API Gateway → Slack Handler (ack <3s) →
 *   async Worker → DevOps Agent create_chat/send_message → Slack post.
 */
export class DevOpsAgentSlack extends Construct {
  readonly apiEndpoint: string;
  readonly workerRoleName: string;
  readonly workerRoleArn: string;

  constructor(scope: Construct, id: string, props: DevOpsAgentSlackProps) {
    super(scope, id);

    const account = cdk.Stack.of(this).account;

    // --- Path A: Webhook Forwarder ---
    const webhookLambda = new lambda.Function(this, 'WebhookForwarder', {
      functionName: `${props.projectName}-webhook-forwarder`,
      runtime: lambda.Runtime.PYTHON_3_12,
      architecture: lambda.Architecture.ARM_64,
      memorySize: 256,
      timeout: cdk.Duration.seconds(30),
      handler: 'handler.lambda_handler',
      code: lambda.Code.fromAsset(path.join(__dirname, '../lambda/webhook-forwarder')),
      logRetention: logs.RetentionDays.ONE_WEEK,
      environment: {
        SECRET_NAME: props.webhookSecretName,
        SERVICE_NAME: props.projectName,
      },
    });
    webhookLambda.addToRolePolicy(new iam.PolicyStatement({
      actions: ['secretsmanager:GetSecretValue'],
      resources: [`${props.webhookSecretArn}*`],
    }));

    // --- Path B: Slack Worker (async agent call) ---
    this.workerRoleName = `${props.projectName}-slack-worker-${props.deploymentId}`;

    const workerRole = new iam.Role(this, 'SlackWorkerRole', {
      roleName: this.workerRoleName,
      assumedBy: new iam.ServicePrincipal('lambda.amazonaws.com'),
      managedPolicies: [
        iam.ManagedPolicy.fromAwsManagedPolicyName('service-role/AWSLambdaBasicExecutionRole'),
      ],
    });
    workerRole.addToPolicy(new iam.PolicyStatement({
      actions: ['secretsmanager:GetSecretValue'],
      resources: [`${props.slackSecretArn}*`],
    }));
    workerRole.addToPolicy(new iam.PolicyStatement({
      actions: ['sts:AssumeRole', 'sts:TagSession'],
      resources: [props.operatorRoleArn],
    }));

    const slackWorkerLambda = new lambda.Function(this, 'SlackWorker', {
      functionName: `${props.projectName}-slack-worker`,
      role: workerRole,
      runtime: lambda.Runtime.PYTHON_3_12,
      architecture: lambda.Architecture.ARM_64,
      memorySize: 256,
      timeout: cdk.Duration.seconds(60),
      handler: 'handler.lambda_handler',
      code: lambda.Code.fromAsset(path.join(__dirname, '../lambda/slack-worker'), {
        bundling: {
          image: lambda.Runtime.PYTHON_3_12.bundlingImage,
          command: [
            'bash', '-c',
            'pip install --no-cache-dir -r requirements.txt -t /asset-output && cp -au . /asset-output',
          ],
          local: {
            tryBundle(outputDir: string) {
              try {
                const { execSync } = require('child_process');
                execSync(
                  `pip install --no-cache-dir -r requirements.txt -t "${outputDir}" && cp -a . "${outputDir}"`,
                  { cwd: path.join(__dirname, '../lambda/slack-worker'), stdio: 'pipe' },
                );
                return true;
              } catch {
                return false;
              }
            },
          },
        },
      }),
      logRetention: logs.RetentionDays.ONE_WEEK,
      environment: {
        SLACK_SECRET_NAME: props.slackSecretName,
      },
    });

    // --- Path B: Slack Handler (ack) ---
    const slackHandlerLambda = new lambda.Function(this, 'SlackHandler', {
      functionName: `${props.projectName}-slack-handler`,
      runtime: lambda.Runtime.PYTHON_3_12,
      architecture: lambda.Architecture.ARM_64,
      memorySize: 256,
      timeout: cdk.Duration.seconds(10),
      handler: 'handler.lambda_handler',
      code: lambda.Code.fromAsset(path.join(__dirname, '../lambda/slack-handler')),
      logRetention: logs.RetentionDays.ONE_WEEK,
      environment: {
        SLACK_SECRET_NAME: props.slackSecretName,
        WORKER_FUNCTION_NAME: slackWorkerLambda.functionName,
      },
    });
    slackHandlerLambda.addToRolePolicy(new iam.PolicyStatement({
      actions: ['secretsmanager:GetSecretValue'],
      resources: [`${props.slackSecretArn}*`],
    }));
    slackWorkerLambda.grantInvoke(slackHandlerLambda);

    // --- API Gateway ---
    const httpApi = new apigatewayv2.HttpApi(this, 'SlackHttpApi', {
      apiName: `${props.projectName}-slack-api`,
    });
    httpApi.addRoutes({
      path: '/slack/events',
      methods: [apigatewayv2.HttpMethod.POST],
      integration: new apigatewayv2_integrations.HttpLambdaIntegration(
        'SlackHandlerIntegration',
        slackHandlerLambda,
      ),
    });

    // --- SNS subscription ---
    const alarmTopic = sns.Topic.fromTopicArn(this, 'AlarmTopic', props.alarmTopicArn);
    alarmTopic.addSubscription(new sns_subs.LambdaSubscription(webhookLambda));

    // --- Outputs ---
    this.apiEndpoint = `${httpApi.apiEndpoint}/slack/events`;
    this.workerRoleArn = `arn:aws:iam::${account}:role/${this.workerRoleName}`;

    new cdk.CfnOutput(this, 'SlackApiEndpoint', {
      value: this.apiEndpoint,
      description: 'Set this as the Slack App Request URL (Events + Slash Commands).',
    });
    new cdk.CfnOutput(this, 'SlackWorkerRoleArn', {
      value: this.workerRoleArn,
      description: 'Slack Worker role ARN (must be allowed by operator role trust policy).',
    });
  }
}
