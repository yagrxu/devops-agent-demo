import json
import logging
import urllib.request

import boto3

logger = logging.getLogger()
logger.setLevel(logging.INFO)


def send_cfn_response(event, context, status, data=None, reason=None, physical_resource_id=None):
    body = json.dumps({
        'Status': status,
        'Reason': reason or f'See CloudWatch Log Stream: {context.log_stream_name}',
        'PhysicalResourceId': physical_resource_id or context.log_stream_name,
        'StackId': event['StackId'],
        'RequestId': event['RequestId'],
        'LogicalResourceId': event['LogicalResourceId'],
        'Data': data or {},
    }).encode('utf-8')
    req = urllib.request.Request(event['ResponseURL'], data=body, method='PUT')
    req.add_header('Content-Type', '')
    req.add_header('Content-Length', str(len(body)))
    urllib.request.urlopen(req)


def handler(event, context):
    logger.info(f'Event: {json.dumps(event)}')
    request_type = event['RequestType']
    props = event['ResourceProperties']
    region = props['Region']
    space_name = props['SpaceName']
    account_id = props['AccountId']

    client = boto3.client('devops-agent', region_name=region)

    try:
        if request_type == 'Create':
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
                serviceId='aws-source',
                configuration={
                    'sourceAws': {
                        'accountId': account_id,
                        'accountType': 'source',
                    },
                },
            )
            assoc_id = assoc_resp['association']['associationId']
            logger.info(f'Associated AWS source: {assoc_id}')

            send_cfn_response(event, context, 'SUCCESS',
                              data={'AgentSpaceId': space_id, 'AssociationId': assoc_id},
                              physical_resource_id=space_id)

        elif request_type == 'Delete':
            space_id = event.get('PhysicalResourceId', '')
            if space_id and not space_id.startswith('LogStream'):
                try:
                    client.delete_agent_space(agentSpaceId=space_id)
                    logger.info(f'Deleted agent space: {space_id}')
                except client.exceptions.ResourceNotFoundException:
                    logger.info(f'Agent space already deleted: {space_id}')
                except Exception as e:
                    logger.warning(f'Error deleting agent space (non-fatal): {e}')
            send_cfn_response(event, context, 'SUCCESS', physical_resource_id=space_id)

        elif request_type == 'Update':
            space_id = event.get('PhysicalResourceId', '')
            send_cfn_response(event, context, 'SUCCESS',
                              data={'AgentSpaceId': space_id},
                              physical_resource_id=space_id)

    except Exception as e:
        logger.error(f'Error: {e}')
        send_cfn_response(event, context, 'FAILED', reason=str(e),
                          physical_resource_id=event.get('PhysicalResourceId', context.log_stream_name))
