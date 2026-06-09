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

        assoc_resp = client.associate_service(
            agentSpaceId=space_id,
            serviceId='aws',
            configuration={
                'sourceAws': {
                    'accountId': account_id,
                    'accountType': 'source',
                    'assumableRoleArn': assume_role_arn,
                },
            },
        )
        assoc_id = assoc_resp['association']['associationId']
        logger.info(f'Associated AWS source: {assoc_id}')

        return {
            'PhysicalResourceId': space_id,
            'Data': {
                'AgentSpaceId': space_id,
                'AssociationId': assoc_id,
            },
        }

    elif request_type == 'Delete':
        space_id = event.get('PhysicalResourceId', '')
        if space_id:
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
        return {
            'PhysicalResourceId': space_id,
            'Data': {'AgentSpaceId': space_id},
        }
