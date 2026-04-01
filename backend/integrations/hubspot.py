# hubspot.py

import os
import json
import secrets
import base64
import asyncio
from urllib.parse import urlencode

import httpx

from fastapi import Request, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse

from redis_client import add_key_value_redis, get_value_redis, delete_key_redis
from integrations.integration_item import IntegrationItem

# --- Startup validation ---
HUBSPOT_CLIENT_ID = os.environ.get('HUBSPOT_CLIENT_ID')
HUBSPOT_CLIENT_SECRET = os.environ.get('HUBSPOT_CLIENT_SECRET')
HUBSPOT_REDIRECT_URI = os.environ.get('HUBSPOT_REDIRECT_URI')

_missing = [
    name for name, val in [
        ('HUBSPOT_CLIENT_ID', HUBSPOT_CLIENT_ID),
        ('HUBSPOT_CLIENT_SECRET', HUBSPOT_CLIENT_SECRET),
        ('HUBSPOT_REDIRECT_URI', HUBSPOT_REDIRECT_URI),
    ]
    if not val
]
if _missing:
    raise RuntimeError(
        f"Missing required environment variables: {', '.join(_missing)}"
    )

SCOPE = os.environ.get(
    'HUBSPOT_SCOPES',
    'crm.objects.contacts.read crm.objects.companies.read crm.objects.deals.read',
)
AUTHORIZATION_BASE_URL = 'https://app.hubspot.com/oauth/authorize'


# --- Functions (implemented in subsequent tasks) ---

async def authorize_hubspot(user_id, org_id):
    state_data = {
        'state': secrets.token_urlsafe(32),
        'user_id': user_id,
        'org_id': org_id,
    }
    encoded_state = base64.urlsafe_b64encode(json.dumps(state_data).encode('utf-8')).decode('utf-8')
    query = urlencode({
        'client_id': HUBSPOT_CLIENT_ID,
        'redirect_uri': HUBSPOT_REDIRECT_URI,
        'response_type': 'code',
        'scope': SCOPE,
        'state': encoded_state,
    })
    auth_url = f'{AUTHORIZATION_BASE_URL}?{query}'
    await add_key_value_redis(f'hubspot_state:{org_id}:{user_id}', json.dumps(state_data), expire=600)
    return JSONResponse(content={"auth_url": auth_url})


async def oauth2callback_hubspot(request: Request):
    if request.query_params.get('error'):
        raise HTTPException(
            status_code=400,
            detail=request.query_params.get('error_description', request.query_params.get('error'))
        )

    code = request.query_params.get('code')
    encoded_state = request.query_params.get('state')

    if not code or not encoded_state:
        raise HTTPException(status_code=400, detail='Missing code or state.')

    try:
        state_data = json.loads(base64.urlsafe_b64decode(encoded_state).decode('utf-8'))
    except Exception as exc:
        raise HTTPException(status_code=400, detail='Invalid state payload.') from exc

    original_state = state_data.get('state')
    user_id = state_data.get('user_id')
    org_id = state_data.get('org_id')

    saved_state = await get_value_redis(f'hubspot_state:{org_id}:{user_id}')

    if not saved_state or original_state != json.loads(saved_state).get('state'):
        raise HTTPException(status_code=400, detail='State does not match.')

    async with httpx.AsyncClient() as client:
        response, _ = await asyncio.gather(
            client.post(
                'https://api.hubapi.com/oauth/v1/token',
                data={
                    'grant_type': 'authorization_code',
                    'code': code,
                    'redirect_uri': HUBSPOT_REDIRECT_URI,
                    'client_id': HUBSPOT_CLIENT_ID,
                    'client_secret': HUBSPOT_CLIENT_SECRET,
                },
                headers={'Content-Type': 'application/x-www-form-urlencoded'},
            ),
            delete_key_redis(f'hubspot_state:{org_id}:{user_id}'),
        )

    if not response.is_success:
        raise HTTPException(status_code=502, detail=response.text)

    await add_key_value_redis(
        f'hubspot_credentials:{org_id}:{user_id}',
        json.dumps(response.json()),
        expire=600,
    )

    return HTMLResponse(content="""<html>
    <script>
        window.close();
    </script>
</html>""")


async def get_hubspot_credentials(user_id, org_id):
    credentials = await get_value_redis(f'hubspot_credentials:{org_id}:{user_id}')
    if not credentials:
        raise HTTPException(status_code=400, detail='No credentials found.')
    return json.loads(credentials)


def create_integration_item_metadata_object(response_json: dict, item_type: str) -> IntegrationItem:
    props = response_json.get('properties', {})

    if item_type == 'Contact':
        name = f"{props.get('firstname', '')} {props.get('lastname', '')}".strip()
        if not name:
            name = props.get('email', '')
    elif item_type == 'Company':
        name = props.get('name', 'Unknown Company')
    else:  # Deal
        name = props.get('dealname', 'Unknown Deal')

    return IntegrationItem(
        id=f"{response_json['id']}_{item_type}",
        name=name,
        type=item_type,
        creation_time=props.get('createdate'),
        last_modified_time=props.get('hs_lastmodifieddate'),
        url=None,
    )


async def fetch_crm_objects(access_token: str, object_type: str, aggregated: list) -> None:
    url = f'https://api.hubapi.com/crm/v3/objects/{object_type}'
    headers = {'Authorization': f'Bearer {access_token}'}
    params = {'limit': 100}

    async with httpx.AsyncClient(timeout=30.0) as client:
        while True:
            response = await client.get(url, headers=headers, params=params)
            if response.status_code != 200:
                raise HTTPException(status_code=response.status_code, detail=response.text)

            data = response.json()
            aggregated.extend(data.get('results', []))

            cursor = data.get('paging', {}).get('next', {}).get('after')
            if not cursor:
                break
            params = {'limit': 100, 'after': cursor}


async def get_items_hubspot(credentials) -> list[IntegrationItem]:
    creds = json.loads(credentials) if isinstance(credentials, str) else credentials
    access_token = creds.get('access_token')

    if not access_token:
        raise HTTPException(status_code=400, detail='Missing access_token in credentials.')

    object_types = [
        ('contacts', 'Contact'),
        ('companies', 'Company'),
        ('deals', 'Deal'),
    ]

    items = []
    for object_type, item_type in object_types:
        aggregated = []
        await fetch_crm_objects(access_token, object_type, aggregated)
        for obj in aggregated:
            items.append(create_integration_item_metadata_object(obj, item_type))

    return items
