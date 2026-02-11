#!/usr/bin/env python3
"""
Coincall API Authentication Module

Handles all API authentication and request signing according to Coincall API v2.0.1 spec.
This module abstracts away authentication details from higher-level modules.

Authentication:
  - Signature prehash includes request parameters as query string format
  - POST: prehash = METHOD + ENDPOINT + ?param1=val1&param2=val2&uuid=key&ts=ts&x-req-ts-diff=diff
  - Signature: HMAC-SHA256(api_secret, prehash).hexdigest().upper()

Content Types:
  - Most POST endpoints: application/json (default)
  - RFQ accept/cancel endpoints: application/x-www-form-urlencoded (use_form_data=True)
"""

import hashlib
import hmac
import json
import time
import logging
import requests
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)


class CoincallAuth:
    """Handles Coincall API authentication and request signing"""

    def __init__(self, api_key: str, api_secret: str, base_url: str):
        """
        Initialize authentication handler
        
        Args:
            api_key: Coincall API key
            api_secret: Coincall API secret
            base_url: Base URL for API (e.g., https://api.coincall.com)
        """
        self.api_key = api_key
        self.api_secret = api_secret
        self.base_url = base_url
        self.session = requests.Session()

    def _create_signature(
        self, 
        method: str, 
        endpoint: str, 
        ts: int, 
        x_req_ts_diff: int = 5000,
        data: Optional[Dict[str, Any]] = None
    ) -> str:
        """Create HMAC SHA256 signature for API request."""
        def flatten_params(d):
            """Flatten dict to sorted list of (key, value) tuples for query string."""
            items = []
            for k, v in sorted(d.items()):
                if v is None:
                    continue
                if isinstance(v, (dict, list)):
                    v = json.dumps(v, separators=(',', ':'))
                items.append((k, str(v)))
            return items
        
        # Build prehash: METHOD + ENDPOINT + ?params&uuid=...&ts=...&x-req-ts-diff=...
        prehash = f'{method}{endpoint}'
        
        if method.upper() == 'POST' and data:
            param_list = flatten_params(data)
            if param_list:
                prehash += '?' + '&'.join(f"{k}={v}" for k, v in param_list)
        
        # Append auth parameters
        auth_suffix = f"uuid={self.api_key}&ts={ts}&x-req-ts-diff={x_req_ts_diff}"
        prehash += ('&' if '?' in prehash else '?') + auth_suffix
        
        # Sign the prehash
        return hmac.new(
            self.api_secret.encode('utf-8'),
            prehash.encode('utf-8'),
            hashlib.sha256
        ).hexdigest().upper()

    def _get_headers(self, method: str, endpoint: str, data: Optional[Dict[str, Any]] = None) -> Dict[str, str]:
        """Get authentication headers for API request."""
        ts = int(time.time() * 1000)
        x_req_ts_diff = 5000
        signature = self._create_signature(method, endpoint, ts, x_req_ts_diff, data)
        
        return {
            'X-CC-APIKEY': self.api_key,
            'sign': signature,
            'ts': str(ts),
            'X-REQ-TS-DIFF': str(x_req_ts_diff),
            'Content-Type': 'application/json'
        }

    def request(
        self, 
        method: str, 
        endpoint: str, 
        data: Optional[Dict[str, Any]] = None,
        use_form_data: bool = False
    ) -> Dict[str, Any]:
        """Make authenticated API request."""
        headers = self._get_headers(method, endpoint, data)
        url = f'{self.base_url}{endpoint}'
        
        try:
            if method.upper() == 'GET':
                response = self.session.get(url, headers=headers)
            elif method.upper() == 'POST':
                if use_form_data and data:
                    # Use form-urlencoded instead of JSON
                    headers['Content-Type'] = 'application/x-www-form-urlencoded'
                    response = self.session.post(url, data=data, headers=headers)
                else:
                    response = self.session.post(url, json=data, headers=headers)
            else:
                raise ValueError(f"Unsupported HTTP method: {method}")
            
            response.raise_for_status()
            return response.json()
        
        except requests.RequestException as e:
            logger.error(f"API request failed: {e}")
            return {'code': 500, 'msg': str(e), 'data': None}

    def get(self, endpoint: str) -> Dict[str, Any]:
        """Make GET request"""
        return self.request('GET', endpoint)

    def post(self, endpoint: str, data: Dict[str, Any], use_form_data: bool = False) -> Dict[str, Any]:
        """Make POST request"""
        return self.request('POST', endpoint, data, use_form_data)

    def is_successful(self, response: Dict[str, Any]) -> bool:
        """Check if API response code is 0 (success)."""
        return response.get('code') == 0
