import json
import time
from dataclasses import replace
from unittest.mock import Mock

import pytest
from itmo_local.auth import Auth
from itmo_local.common import SafeError
from itmo_local.config import Settings


class MemoryProtected:
    def __init__(self):
        self.values = {}

    def get(self,key):
        return self.values.get(key)

    def get_tokens(self):
        return json.loads(self.values['itmo_tokens']) if 'itmo_tokens' in self.values else None

    def save_tokens(self,tokens):
        self.values['itmo_tokens'] = json.dumps(tokens)

    def forget(self,key):
        self.values.pop(key,None)


class MockHttp:
    def __init__(self):
        self.post_count = 0
        self.data = []

    def json(self, method,url,*,allowed_host,data=None,**kwargs):
        if method == 'GET':
            return {'issuer':'https://id.itmo.ru/auth/realms/itmo',
                    'authorization_endpoint':'https://id.itmo.ru/auth/realms/itmo/protocol/openid-connect/auth',
                    'token_endpoint':'https://id.itmo.ru/auth/realms/itmo/protocol/openid-connect/token',
                    'code_challenge_methods_supported':['S256']}
        self.post_count += 1
        self.data.append(data)
        return {'access_token':f'access-{self.post_count}', 'refresh_token':f'refresh-{self.post_count}',
                'expires_in':3600,'refresh_expires_in':8000, 'token_type':'Bearer'}


def test_single_use_refresh_is_rotated(settings):
    protected=MemoryProtected()
    http=MockHttp()
    authorized=replace(settings, mode='myitmo', client_id='approved-public-client')
    auth=Auth(authorized,http,protected)
    with pytest.raises(SafeError, match='itmo_login_required'):
        auth.access_token()
    auth._save_response({'access_token':'old-access', 'refresh_token':'old-refresh',
                         'expires_in':10,'refresh_expires_in':7200},time.time()-100)
    assert auth.access_token() == 'access-1'
    assert http.data[0]['refresh_token'] == 'old-refresh'
    assert protected.get_tokens()['refresh_token'] == 'refresh-1'
    assert auth.access_token(force_refresh=True) == 'access-2'
    assert http.data[1]['refresh_token'] == 'refresh-1'
    assert protected.get_tokens()['refresh_token'] == 'refresh-2'


def test_missing_refresh_blocks_live_session(settings):
    protected=MemoryProtected()
    http=MockHttp()
    auth=Auth(replace(settings, client_id='approved-public-client'),http,protected)
    auth._save_response({'access_token':'old-access','expires_in':3600},time.time()-5000)
    with pytest.raises(SafeError, match='refresh_token_missing'):
        auth.access_token()


def test_client_mismatch_does_not_use_token(settings):
    protected=MemoryProtected()
    http=MockHttp()
    auth=Auth(replace(settings,client_id='approved-public-client'),http,protected)
    auth._save_response({'access_token':'access','refresh_token':'refresh','expires_in':3600},time.time())
    other=Auth(replace(settings,client_id='another-client'),http,protected)
    with pytest.raises(SafeError, match='token_configuration_changed'):
        other.access_token()


def test_no_unapproved_client_configuration(settings):
    auth=Auth(settings,MockHttp(),MemoryProtected())
    with pytest.raises(SafeError, match='permitted_itmo_oauth_client_required'):
        auth.discovery()
