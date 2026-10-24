"""MTN Mobile Money (MoMo) Collections API client.

Docs: https://momodeveloper.mtn.com/api-details#api=collection

Credentials come from the MoMo developer portal: a subscription key,
a provisioned API user (UUID) and its API key. Environment values are
read from Django settings, which source them from env vars:

    MTN_MOMO_SUBSCRIPTION_KEY
    MTN_MOMO_API_USER
    MTN_MOMO_API_KEY
    MTN_MOMO_TARGET_ENV      'sandbox' (default) or 'mtnuganda'
    MTN_MOMO_BASE_URL        overrides the default host
"""

import logging

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

SANDBOX_BASE_URL = 'https://sandbox.momodeveloper.mtn.com'
PRODUCTION_BASE_URL = 'https://proxy.momoapi.mtn.com'
TIMEOUT = 30


class MoMoError(Exception):
    """Raised when the MoMo API call fails."""


def normalize_msisdn(phone, sandbox=False):
    """Normalize a Ugandan phone number to MTN's 2567XXXXXXXX MSISDN form.

    Accepts 07XXXXXXXX, +2567XXXXXXXX, 2567XXXXXXXX. In sandbox mode any
    9-15 digit MSISDN is accepted so the documented MTN test numbers
    (e.g. 46733123450 -> SUCCESSFUL) can be used.
    """
    digits = ''.join(c for c in str(phone or '') if c.isdigit())
    if digits.startswith('00256'):
        digits = digits[2:]
    elif digits.startswith('256'):
        pass
    elif digits.startswith('0'):
        digits = '256' + digits[1:]
    if digits.startswith('2567') and len(digits) == 12:
        return digits
    if sandbox and 9 <= len(digits) <= 15:
        return digits
    raise MoMoError(
        'Enter a valid MTN Uganda number, e.g. 0772 123456 or +256772123456.')


class MtnMoMoClient:
    def __init__(self):
        self.subscription_key = getattr(settings, 'MTN_MOMO_SUBSCRIPTION_KEY', '') or ''
        self.api_user = getattr(settings, 'MTN_MOMO_API_USER', '') or ''
        self.api_key = getattr(settings, 'MTN_MOMO_API_KEY', '') or ''
        self.target_env = getattr(settings, 'MTN_MOMO_TARGET_ENV', 'sandbox') or 'sandbox'
        self.base_url = getattr(settings, 'MTN_MOMO_BASE_URL', '') or (
            SANDBOX_BASE_URL if self.target_env == 'sandbox' else PRODUCTION_BASE_URL)

    @property
    def is_configured(self):
        return bool(self.subscription_key and self.api_user and self.api_key)

    def _headers(self, token=None, reference_id=None):
        headers = {'Ocp-Apim-Subscription-Key': self.subscription_key}
        if token:
            headers['Authorization'] = f'Bearer {token}'
        if reference_id:
            headers['X-Reference-Id'] = reference_id
        headers['X-Target-Environment'] = self.target_env
        return headers

    def get_access_token(self):
        if not self.is_configured:
            raise MoMoError('MTN MoMo is not configured (missing API credentials).')
        try:
            response = requests.post(
                f'{self.base_url}/collection/token/',
                auth=(self.api_user, self.api_key),
                headers=self._headers(),
                timeout=TIMEOUT,
            )
        except requests.RequestException as exc:
            raise MoMoError(f'Could not reach MTN MoMo: {exc}') from exc
        if response.status_code != 200:
            raise MoMoError(f'MTN authentication failed (HTTP {response.status_code}).')
        return response.json().get('access_token', '')

    def request_to_pay(self, reference_id, *, amount, currency, msisdn,
                       payer_message='', payee_note=''):
        """Send a request-to-pay. Returns the requests response (202 on success)."""
        token = self.get_access_token()
        payload = {
            'amount': str(amount),
            'currency': currency,
            'externalId': reference_id,
            'payer': {'partyIdType': 'MSISDN', 'partyId': msisdn},
            'payerMessage': payer_message[:160],
            'payeeNote': payee_note[:160],
        }
        try:
            response = requests.post(
                f'{self.base_url}/collection/v1_0/requesttopay',
                json=payload,
                headers={**self._headers(token, reference_id),
                         'Content-Type': 'application/json'},
                timeout=TIMEOUT,
            )
        except requests.RequestException as exc:
            raise MoMoError(f'Could not reach MTN MoMo: {exc}') from exc
        if response.status_code != 202:
            detail = ''
            try:
                detail = response.json().get('message', '')
            except ValueError:
                detail = response.text[:200]
            raise MoMoError(
                f'MTN rejected the payment request (HTTP {response.status_code})'
                f'{": " + detail if detail else "."}')
        return response

    def get_payment_status(self, reference_id):
        """Poll request-to-pay status. Returns the decoded JSON body."""
        token = self.get_access_token()
        try:
            response = requests.get(
                f'{self.base_url}/collection/v1_0/requesttopay/{reference_id}',
                headers=self._headers(token),
                timeout=TIMEOUT,
            )
        except requests.RequestException as exc:
            raise MoMoError(f'Could not reach MTN MoMo: {exc}') from exc
        if response.status_code != 200:
            raise MoMoError(
                f'MTN status check failed (HTTP {response.status_code}).')
        return response.json()
