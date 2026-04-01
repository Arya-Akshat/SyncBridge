import abc
import json
import base64
import secrets
import asyncio
import httpx
from datetime import datetime
from typing import List, Optional, Any
from fastapi import Request, HTTPException
from fastapi.responses import HTMLResponse
from redis_client import add_key_value_redis, get_value_redis, delete_key_redis


class UnifiedItem:
    def __init__(
        self,
        id: str,
        name: str,
        source: str,
        type: str,
        created_at: Optional[datetime],
        updated_at: Optional[datetime],
        metadata: Any
    ):
        self.id = id
        self.name = name
        self.source = source
        self.type = type
        self.created_at = created_at
        self.updated_at = updated_at
        self.metadata = metadata

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "source": self.source,
            "type": self.type,
            "created_at": self.created_at.isoformat() if isinstance(self.created_at, datetime) else self.created_at,
            "updated_at": self.updated_at.isoformat() if isinstance(self.updated_at, datetime) else self.updated_at,
            "metadata": self.metadata
        }

class BaseIntegration(abc.ABC):
    def __init__(self, source_name: str, client_id: str, client_secret: str, redirect_uri: str, scope: str, authorization_url: str, token_url: str):
        self.source_name = source_name
        self.client_id = client_id
        self.client_secret = client_secret
        self.redirect_uri = redirect_uri
        self.scope = scope
        self.authorization_url = authorization_url
        self.token_url = token_url

    async def authorize(self, user_id: str, org_id: str):
        state_data = {
            'state': secrets.token_urlsafe(32),
            'user_id': user_id,
            'org_id': org_id,
        }
        encoded_state = base64.urlsafe_b64encode(json.dumps(state_data).encode('utf-8')).decode('utf-8')
        
        auth_url = (
            f'{self.authorization_url}'
            f'?client_id={self.client_id}'
            f'&redirect_uri={self.redirect_uri}'
            f'&response_type=code'
            f'&scope={self.scope}'
            f'&state={encoded_state}'
        )
        
        # In reality, this specific format might vary slightly per integration (Airtable uses code_challenge etc)
        # But for now we just build a generic one. Subclasses can override if they need pkce.
        
        await add_key_value_redis(f'{self.source_name}_state:{org_id}:{user_id}', json.dumps(state_data), expire=600)
        return auth_url

    async def callback(self, request: Request):
        if request.query_params.get('error'):
            raise HTTPException(
                status_code=400,
                detail=request.query_params.get('error_description', request.query_params.get('error'))
            )
        
        code = request.query_params.get('code')
        encoded_state = request.query_params.get('state')
        state_data = json.loads(base64.urlsafe_b64decode(encoded_state).decode('utf-8'))

        user_id = state_data.get('user_id')
        org_id = state_data.get('org_id')
        original_state = state_data.get('state')

        saved_state_str = await get_value_redis(f'{self.source_name}_state:{org_id}:{user_id}')
        if not saved_state_str or original_state != json.loads(saved_state_str).get('state'):
            raise HTTPException(status_code=400, detail='State does not match.')

        token_data = await self.exchange_code(code)
        
        # Save token
        await add_key_value_redis(
            f'{self.source_name}_credentials:{org_id}:{user_id}',
            json.dumps(token_data),
            expire=600,
        )

        await delete_key_redis(f'{self.source_name}_state:{org_id}:{user_id}')
        
        return HTMLResponse(content='<html><script>window.close();</script></html>')

    async def exchange_code(self, code: str) -> dict:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                self.token_url,
                data={
                    'grant_type': 'authorization_code',
                    'code': code,
                    'redirect_uri': self.redirect_uri,
                    'client_id': self.client_id,
                    'client_secret': self.client_secret,
                },
                headers={'Content-Type': 'application/x-www-form-urlencoded'},
            )
            
            if not response.is_success:
                raise HTTPException(status_code=502, detail=response.text)
            
            return response.json()

    async def get_credentials(self, user_id: str, org_id: str):
        credentials = await get_value_redis(f'{self.source_name}_credentials:{org_id}:{user_id}')
        if not credentials:
            raise HTTPException(status_code=400, detail='No credentials found.')
        await delete_key_redis(f'{self.source_name}_credentials:{org_id}:{user_id}')
        return json.loads(credentials)

    async def refresh_token(self, refresh_token: str) -> dict:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                self.token_url,
                data={
                    'grant_type': 'refresh_token',
                    'refresh_token': refresh_token,
                    'client_id': self.client_id,
                    'client_secret': self.client_secret,
                },
                headers={'Content-Type': 'application/x-www-form-urlencoded'}
            )
            if not response.is_success:
                raise HTTPException(status_code=response.status_code, detail=response.text)
            return response.json()

    # Exponential backoff resilience wrapper
    async def make_api_request(self, method: str, url: str, headers: dict, params: dict = None, max_retries: int = 3):
        import asyncio
        async with httpx.AsyncClient() as client:
            for attempt in range(max_retries):
                response = await client.request(method, url, headers=headers, params=params)
                if response.status_code == 429 or response.status_code >= 500:
                    wait_time = (2 ** attempt)
                    await asyncio.sleep(wait_time)
                    continue
                response.raise_for_status()
                return response.json()
            raise HTTPException(status_code=502, detail="Max retries reached")

    @abc.abstractmethod
    async def fetch_items(self, credentials: str) -> List[UnifiedItem]:
        pass
