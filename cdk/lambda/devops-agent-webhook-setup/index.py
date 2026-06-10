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
    agent_space_id = props['AgentSpaceId']

    client = boto3.client('devops-agent', region_name=region)

    if request_type in ('Create', 'Update'):
        # Step 1: Register or find eventChannel service
        svc_resp = client.list_services(filterServiceType='eventChannel')
        services = svc_resp.get('services', [])
        if services:
            event_svc_id = services[0]['serviceId']
            logger.info(f'Found existing eventChannel service: {event_svc_id}')
        else:
            reg_resp = client.register_service(
                service='eventChannel',
                serviceDetails={'eventChannel': {'type': 'webhook'}},
            )
            event_svc_id = reg_resp['serviceId']
            logger.info(f'Registered eventChannel service: {event_svc_id}')

        # Step 2: Associate with agent space (idempotent)
        assoc_id = None
        try:
            assoc_resp = client.associate_service(
                agentSpaceId=agent_space_id,
                serviceId=event_svc_id,
                configuration={'eventChannel': {}},
            )
            assoc_id = assoc_resp['association']['associationId']
            logger.info(f'Created event channel association: {assoc_id}')
        except Exception as e:
            if 'already exists' in str(e).lower():
                # Find existing association
                assocs = client.list_associations(agentSpaceId=agent_space_id)
                for assoc in assocs.get('associations', []):
                    if assoc.get('serviceId') == event_svc_id:
                        assoc_id = assoc['associationId']
                        break
                logger.info(f'Event channel already associated: {assoc_id}')
            else:
                raise

        # Step 3: Get webhook URL
        webhook_url = 'N/A'
        if assoc_id:
            webhooks_resp = client.list_webhooks(
                agentSpaceId=agent_space_id,
                associationId=assoc_id,
            )
            logger.info(f'Webhooks response: {json.dumps(webhooks_resp, default=str)}')
            webhooks = webhooks_resp.get('webhooks', [])
            if webhooks:
                webhook_url = webhooks[0].get('webhookUrl', 'N/A')

        return {
            'PhysicalResourceId': assoc_id or 'no-association',
            'Data': {
                'WebhookUrl': webhook_url,
                'AssociationId': assoc_id or '',
                'ServiceId': event_svc_id,
            },
        }

    elif request_type == 'Delete':
        # Don't delete the service registration (account-level, may be shared)
        # Just disassociate from this space
        phys_id = event.get('PhysicalResourceId', '')
        if phys_id and phys_id != 'no-association':
            try:
                client.disassociate_service(
                    agentSpaceId=agent_space_id,
                    associationId=phys_id,
                )
                logger.info(f'Disassociated event channel: {phys_id}')
            except Exception as e:
                logger.warning(f'Disassociate failed (non-fatal): {e}')
        return {'PhysicalResourceId': phys_id}
