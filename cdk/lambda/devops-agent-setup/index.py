import json
import logging

import boto3

logger = logging.getLogger()
logger.setLevel(logging.INFO)


def handler(event, context):
    logger.info(f'Event: {json.dumps(event)}')
    request_type = event['RequestType']
    props = event['ResourceProperties']
    region = props['Region']
    space_name = props['SpaceName']
    account_id = props['AccountId']
    assume_role_arn = props['AssumeRoleArn']
    operator_role_arn = props['OperatorRoleArn']

    client = boto3.client('devops-agent', region_name=region)

    if request_type == 'Create':
        # Handle re-creation: if space already exists from a failed rollback, reuse it
        space_id = None
        try:
            list_resp = client.list_agent_spaces()
            for space in list_resp.get('agentSpaces', []):
                if space.get('name') == space_name:
                    space_id = space['agentSpaceId']
                    logger.info(f'Found existing agent space: {space_id}')
                    break
        except Exception as e:
            logger.warning(f'Could not list spaces: {e}')

        if not space_id:
            resp = client.create_agent_space(
                name=space_name,
                description='QuickMart flash-sale cascade demo for HK Summit 2026',
                locale='en',
                tags={
                    'Project': 'hk-summit-2026',
                    'Purpose': 'demo',
                },
            )
            space_id = resp['agentSpace']['agentSpaceId']
            logger.info(f'Created agent space: {space_id}')

        try:
            assoc_resp = client.associate_service(
                agentSpaceId=space_id,
                serviceId='aws',
                configuration={
                    'aws': {
                        'accountId': account_id,
                        'accountType': 'monitor',
                        'assumableRoleArn': assume_role_arn,
                    },
                },
            )
            assoc_id = assoc_resp['association']['associationId']
            logger.info(f'Associated AWS source: {assoc_id}')
        except client.exceptions.ValidationException as e:
            if 'already exists' in str(e):
                logger.info(f'AWS association already exists, skipping')
                assoc_id = 'existing'
            else:
                raise

        try:
            client.enable_operator_app(
                agentSpaceId=space_id,
                authFlow='iam',
                operatorAppRoleArn=operator_role_arn,
            )
            logger.info(f'Enabled operator app with role: {operator_role_arn}')
        except Exception as e:
            if 'already' in str(e).lower() or 'conflict' in str(e).lower():
                logger.info(f'Operator app already enabled, skipping')
            else:
                raise

        # Create eventChannel association to get a webhook endpoint
        webhook_url = 'N/A'
        event_assoc_id = ''
        try:
            event_assoc_resp = client.associate_service(
                agentSpaceId=space_id,
                serviceId='event-channel',
                configuration={'eventChannel': {}},
            )
            event_assoc_id = event_assoc_resp['association']['associationId']
            logger.info(f'Created event channel association: {event_assoc_id}')
            logger.info(f'Event channel response: {json.dumps(event_assoc_resp, default=str)}')

            webhooks_resp = client.list_webhooks(
                agentSpaceId=space_id,
                associationId=event_assoc_id,
            )
            logger.info(f'Webhooks: {json.dumps(webhooks_resp, default=str)}')
            if webhooks_resp.get('webhooks'):
                webhook_url = webhooks_resp['webhooks'][0].get('webhookUrl', 'N/A')
        except Exception as e:
            logger.warning(f'Event channel/webhook setup failed (non-fatal): {e}')

        return {
            'PhysicalResourceId': space_id,
            'Data': {
                'AgentSpaceId': space_id,
                'AssociationId': assoc_id,
                'OperatorRoleArn': operator_role_arn,
                'WebhookUrl': webhook_url,
                'EventChannelAssociationId': event_assoc_id,
            },
        }

    elif request_type == 'Delete':
        space_id = event.get('PhysicalResourceId', '')
        if space_id:
            try:
                client.disable_operator_app(agentSpaceId=space_id)
                logger.info(f'Disabled operator app for space: {space_id}')
            except Exception as e:
                logger.warning(f'Error disabling operator app (non-fatal): {e}')
            try:
                client.delete_agent_space(agentSpaceId=space_id)
                logger.info(f'Deleted agent space: {space_id}')
            except client.exceptions.ResourceNotFoundException:
                logger.info(f'Agent space already deleted: {space_id}')
            except Exception as e:
                logger.warning(f'Error deleting agent space (non-fatal): {e}')
        return {'PhysicalResourceId': space_id}

    elif request_type == 'Update':
        space_id = event.get('PhysicalResourceId', '')
        # Re-run the full setup to populate all Data attributes
        webhook_url = 'N/A'
        try:
            # List associations to find event-channel
            assocs = client.list_associations(agentSpaceId=space_id)
            for assoc in assocs.get('associations', []):
                if assoc.get('serviceId') == 'event-channel':
                    webhooks_resp = client.list_webhooks(
                        agentSpaceId=space_id,
                        associationId=assoc['associationId'],
                    )
                    if webhooks_resp.get('webhooks'):
                        webhook_url = webhooks_resp['webhooks'][0].get('webhookUrl', 'N/A')
                    break
            else:
                # No event-channel yet, create one
                event_assoc_resp = client.associate_service(
                    agentSpaceId=space_id,
                    serviceId='event-channel',
                    configuration={'eventChannel': {}},
                )
                event_assoc_id = event_assoc_resp['association']['associationId']
                webhooks_resp = client.list_webhooks(
                    agentSpaceId=space_id,
                    associationId=event_assoc_id,
                )
                if webhooks_resp.get('webhooks'):
                    webhook_url = webhooks_resp['webhooks'][0].get('webhookUrl', 'N/A')
        except Exception as e:
            logger.warning(f'Webhook lookup on update failed (non-fatal): {e}')

        return {
            'PhysicalResourceId': space_id,
            'Data': {
                'AgentSpaceId': space_id,
                'OperatorRoleArn': operator_role_arn,
                'WebhookUrl': webhook_url,
            },
        }
