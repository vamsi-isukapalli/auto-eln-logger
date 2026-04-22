"""eLabFTW API client for the BMD ELN logger."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import List, Optional

import requests
import urllib3

ELABFTW_URL = os.environ.get('ELABFTW_URL', '').strip()
ELABFTW_TOKEN = os.environ.get('ELABFTW_TOKEN', '').strip()
VERIFY_SSL = os.environ.get('ELABFTW_VERIFY_SSL', 'true').lower() in ('1', 'true', 'yes')
REQUEST_TIMEOUT = int(os.environ.get('ELABFTW_TIMEOUT', '30'))

if not VERIFY_SSL:
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

STATUS_RUNNING = int(os.environ.get('ELABFTW_STATUS_RUNNING', '1'))
STATUS_SUCCESS = int(os.environ.get('ELABFTW_STATUS_SUCCESS', '2'))
STATUS_REDO = int(os.environ.get('ELABFTW_STATUS_REDO', '3'))
STATUS_FAIL = int(os.environ.get('ELABFTW_STATUS_FAIL', '4'))
STATUS_QUEUED = int(os.environ.get('ELABFTW_STATUS_QUEUED', str(STATUS_RUNNING)))


class ElabFTWClient:
    """Minimal eLabFTW REST API client."""

    def __init__(self, url: Optional[str] = None, token: Optional[str] = None, verify_ssl: Optional[bool] = None):
        resolved_url = (url if url is not None else ELABFTW_URL).strip()
        resolved_token = (token if token is not None else ELABFTW_TOKEN).strip()
        resolved_verify = VERIFY_SSL if verify_ssl is None else verify_ssl
        if not resolved_url:
            raise RuntimeError('ELABFTW_URL is not set')
        if not resolved_token:
            raise RuntimeError('ELABFTW_TOKEN is not set')
        self.base = resolved_url.rstrip('/') + '/api/v2'
        self.headers = {
            'Authorization': resolved_token,
            'Content-Type': 'application/json',
            'Accept': 'application/json',
        }
        self.verify = resolved_verify

    def _req(self, method: str, path: str, **kwargs):
        url = f"{self.base}/{path.lstrip('/')}"
        response = requests.request(
            method=method,
            url=url,
            headers=self.headers,
            verify=self.verify,
            timeout=REQUEST_TIMEOUT,
            **kwargs,
        )
        if not response.ok:
            raise RuntimeError(f'eLabFTW API error {response.status_code}: {response.text[:300]}')
        return response

    def get_experiment(self, exp_id: int) -> dict:
        return self._req('get', f'/experiments/{exp_id}').json()

    def _set_experiment_status(self, exp_id: int, status_id: int):
        self._req('patch', f'/experiments/{exp_id}', json={'status': int(status_id)})

    def create_experiment(self, title: str, body: str, tags: Optional[List[str]] = None, metadata: Optional[dict] = None) -> int:
        payload = {
            'title': title,
            'body': body,
        }
        if metadata:
            payload['metadata'] = json.dumps({'extra_fields': metadata})
        response = self._req('post', '/experiments', json=payload)
        location = response.headers.get('Location', '')
        if not location:
            raise RuntimeError('eLabFTW did not return a Location header for the created experiment')
        exp_id = int(location.rstrip('/').split('/')[-1])
        if tags:
            for tag in tags:
                self._req('post', f'/experiments/{exp_id}/tags', json={'tag': tag})
        self._set_experiment_status(exp_id, STATUS_QUEUED)
        return exp_id

    def update_experiment(self, exp_id: int, title: Optional[str] = None, body: Optional[str] = None,
                          status_id: Optional[int] = None, metadata: Optional[dict] = None):
        payload: dict = {}
        if title is not None:
            payload['title'] = title
        if body is not None:
            payload['body'] = body
        if metadata is not None:
            payload['metadata'] = json.dumps({'extra_fields': metadata})
        if payload:
            self._req('patch', f'/experiments/{exp_id}', json=payload)
        if status_id is not None:
            self._set_experiment_status(exp_id, int(status_id))

    def upload_file(self, exp_id: int, file_path: str, comment: str = ''):
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(file_path)
        headers = {k: v for k, v in self.headers.items() if k != 'Content-Type'}
        with open(path, 'rb') as handle:
            response = requests.post(
                f"{self.base}/experiments/{exp_id}/uploads",
                headers=headers,
                files={'file': (path.name, handle)},
                data={'comment': comment},
                verify=self.verify,
                timeout=REQUEST_TIMEOUT,
            )
        if not response.ok:
            raise RuntimeError(f'Upload failed ({response.status_code}): {response.text[:300]}')

    def add_comment(self, exp_id: int, comment: str):
        self._req('post', f'/experiments/{exp_id}/comments', json={'comment': comment})

    def mark_running(self, exp_id: int):
        self.update_experiment(exp_id, status_id=STATUS_RUNNING)

    def mark_completed(self, exp_id: int, success: bool):
        self.update_experiment(exp_id, status_id=STATUS_SUCCESS if success else STATUS_FAIL)


if __name__ == '__main__':
    client = ElabFTWClient()
    print(f'Connecting to: {client.base}')
    client._req('get', '/experiments?limit=1')
    print('✅ Connection OK')
