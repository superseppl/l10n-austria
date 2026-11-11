# Copyright 2026 Sebastian Bader
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
"""Thin wrapper around the fiskaly SIGN AT (Austria RKSV) REST API.

Grounded on fiskaly's published SIGN AT OpenAPI specification (v1.2.7,
https://workspace.fiskaly.com/api/rksv/v1, retrieved 2026-11).

Two properties of this API are easy to get wrong and cost a debugging cycle:

* SIGN AT serves TEST and LIVE from the *same* base URL. The environment is a
  property of the API key (the ``env`` claim of the issued JWT, mirrored on
  every resource as ``_env``). There is nothing to switch in the client.
* ``schema.standard_v1`` requires ``amounts_per_vat_rate`` *and*
  ``line_items``, and sets ``additionalProperties: false``. There is no
  ``receipt`` wrapper - that nesting belongs to a different fiskaly product.
"""
import logging
from decimal import ROUND_HALF_UP, Decimal

import requests

_logger = logging.getLogger(__name__)

BASE_URL = "https://rksv.fiskaly.com/api/v1"

TWO_PLACES = Decimal("0.01")

RECEIPT_TYPES = ("NORMAL", "CANCELLATION", "TRAINING")


def format_amount(value):
    """Render a monetary value the way fiskaly expects it: a quoted decimal
    with exactly two places and no thousands separator ("1234.50")."""
    return str(Decimal(str(value or 0)).quantize(TWO_PLACES, rounding=ROUND_HALF_UP))


def format_quantity(value):
    """Quantities accept 1-64 decimals; normalise so we send "1", not "1.0000"."""
    return format(Decimal(str(value or 0)).normalize(), "f")


class FiskalyApiError(Exception):
    def __init__(self, message, status_code=None, payload=None):
        super().__init__(message)
        self.status_code = status_code
        self.payload = payload

    @property
    def code(self):
        """The machine readable error code fiskaly returns, when it sends one."""
        if isinstance(self.payload, dict):
            return self.payload.get("code") or self.payload.get("error")
        return None

    @property
    def detail(self):
        if isinstance(self.payload, dict):
            return self.payload.get("message") or self.payload.get("error")
        return None


class FiskalyClient:
    """Stateless helper: callers pass credentials/tokens in, nothing is cached
    on the instance so it is safe to build a fresh one per call from
    res.company fields."""

    def __init__(self, api_key, api_secret, access_token=None, timeout=20):
        self.api_key = api_key
        self.api_secret = api_secret
        self.access_token = access_token
        self.refresh_token = None
        self.timeout = timeout
        # Populated by authenticate(); useful for surfacing "you are pointed at
        # TEST" in the UI instead of silently signing worthless receipts.
        self.environment = None
        self.organization_id = None

    # -- low level -----------------------------------------------------
    def _headers(self):
        headers = {"Content-Type": "application/json"}
        if self.access_token:
            headers["Authorization"] = f"Bearer {self.access_token}"
        return headers

    def _request(self, method, path, json=None, auth_required=True, retry=True):
        if auth_required and not self.access_token:
            self.authenticate()
        url = f"{BASE_URL}{path}"
        resp = requests.request(
            method, url, json=json, headers=self._headers(), timeout=self.timeout
        )
        if resp.status_code == 401 and auth_required and retry and self.access_token:
            # A stored access token expires after ~24h; re-authenticate once
            # instead of failing a sale because of a stale token.
            _logger.info("fiskaly rejected the stored access token, re-authenticating")
            self.access_token = None
            self.authenticate()
            return self._request(method, path, json=json, auth_required=auth_required, retry=False)
        if resp.status_code >= 400:
            try:
                payload = resp.json()
            except ValueError:
                payload = resp.text
            _logger.error(
                "fiskaly %s %s failed: %s %s", method, path, resp.status_code, payload
            )
            raise FiskalyApiError(
                f"fiskaly API error {resp.status_code} on {method} {path}: {payload}",
                status_code=resp.status_code,
                payload=payload,
            )
        if resp.content:
            return resp.json()
        return {}

    # -- auth ------------------------------------------------------------
    def authenticate(self):
        """POST /auth -> {access_token, refresh_token, access_token_claims, ...}.

        The caller is responsible for persisting the token on res.company so it
        can be reused across requests instead of authenticating per receipt.
        """
        data = self._request(
            "POST",
            "/auth",
            json={"api_key": self.api_key, "api_secret": self.api_secret},
            auth_required=False,
        )
        self.access_token = data.get("access_token")
        self.refresh_token = data.get("refresh_token")
        claims = data.get("access_token_claims") or {}
        self.environment = claims.get("env")
        # The claims object names it organization_id; the raw JWT calls the
        # same value "organization". Accept both.
        self.organization_id = claims.get("organization_id") or claims.get("organization")
        return data

    def fon_auth(self, fon_participant_id, fon_user_id, fon_user_pin):
        """PUT /fon/auth - links the organization to FinanzOnline using the
        credentials of a dedicated 'Cash Register Webservice User' created in
        FinanzOnline (not a personal FinanzOnline login).

        fiskaly keeps the link; this has to be done once, not per register.
        """
        return self._request(
            "PUT",
            "/fon/auth",
            json={
                "fon_participant_id": fon_participant_id,
                "fon_user_id": fon_user_id,
                "fon_user_pin": fon_user_pin,
            },
        )

    def fon_status(self):
        return self._request("GET", "/fon/auth")

    # -- signature creation unit -----------------------------------------
    def scu_create(self, scu_id, vat_id=None, tax_id=None, gln=None, legal_entity_name=None):
        """PUT /signature-creation-unit/{id}.

        Exactly one of vat_id / tax_id / gln identifies the legal entity; the
        API accepts ``{"vat_id": "ATU12345678"}``, ``{"tax_id": "..."}`` or
        ``{"gln": "..."}`` - never a bare string.
        """
        legal_entity_id = {}
        if vat_id:
            legal_entity_id["vat_id"] = vat_id
        elif tax_id:
            legal_entity_id["tax_id"] = tax_id
        elif gln:
            legal_entity_id["gln"] = gln
        else:
            raise ValueError("One of vat_id, tax_id or gln is required")
        payload = {"legal_entity_id": legal_entity_id}
        if legal_entity_name:
            payload["legal_entity_name"] = legal_entity_name
        return self._request("PUT", f"/signature-creation-unit/{scu_id}", json=payload)

    def scu_get(self, scu_id):
        return self._request("GET", f"/signature-creation-unit/{scu_id}")

    def scu_initialize(self, scu_id):
        """PATCH state=INITIALIZED. Registers the SCU with FinanzOnline as a
        side effect, and therefore requires a successful /fon/auth first."""
        return self._request(
            "PATCH",
            f"/signature-creation-unit/{scu_id}",
            json={"state": "INITIALIZED"},
        )

    def scu_decommission(self, scu_id):
        return self._request(
            "PATCH",
            f"/signature-creation-unit/{scu_id}",
            json={"state": "DECOMMISSIONED"},
        )

    # -- cash register ----------------------------------------------------
    def cash_register_create(self, cash_register_id, description):
        return self._request(
            "PUT",
            f"/cash-register/{cash_register_id}",
            json={"description": description},
        )

    def cash_register_register(self, cash_register_id):
        return self._request(
            "PATCH",
            f"/cash-register/{cash_register_id}",
            json={"state": "REGISTERED"},
        )

    def cash_register_initialize(self, cash_register_id):
        """Creates and validates the INITIALIZATION (Startbeleg) receipt."""
        return self._request(
            "PATCH",
            f"/cash-register/{cash_register_id}",
            json={"state": "INITIALIZED"},
        )

    def cash_register_decommission(self, cash_register_id):
        """Creates the DECOMMISSION receipt automatically. Required before a
        cash register is retired for good (e.g. switching providers)."""
        return self._request(
            "PATCH",
            f"/cash-register/{cash_register_id}",
            json={"state": "DECOMMISSIONED"},
        )

    def cash_register_get(self, cash_register_id):
        return self._request("GET", f"/cash-register/{cash_register_id}")

    # -- receipts -----------------------------------------------------------
    @staticmethod
    def build_receipt_payload(
        receipt_type, amounts_per_vat_rate, amounts_per_payment_type, line_items, metadata=None
    ):
        """Assemble the PUT /receipt body and normalise the numbers.

        Kept separate from the request so it can be unit tested without
        network access - the exact shape here is what the API validates.
        """
        if receipt_type not in RECEIPT_TYPES:
            raise ValueError(f"Unsupported receipt_type: {receipt_type}")
        if not amounts_per_vat_rate:
            raise ValueError("amounts_per_vat_rate must not be empty")
        if not line_items:
            raise ValueError("line_items must not be empty (required by standard_v1)")

        standard_v1 = {
            "amounts_per_vat_rate": [
                {"vat_rate": row["vat_rate"], "amount": format_amount(row["amount"])}
                for row in amounts_per_vat_rate
            ],
            "line_items": [
                {
                    "quantity": format_quantity(item.get("quantity", 1)),
                    "text": (item.get("text") or "")[:255],
                    "price_per_unit": format_amount(item.get("price_per_unit")),
                }
                for item in line_items
            ],
        }
        if amounts_per_payment_type is not None:
            standard_v1["amounts_per_payment_type"] = [
                {"payment_type": row["payment_type"], "amount": format_amount(row["amount"])}
                for row in amounts_per_payment_type
            ]

        payload = {
            "receipt_type": receipt_type,
            "schema": {"standard_v1": standard_v1},
        }
        if metadata:
            payload["metadata"] = {
                str(key)[:40]: str(value)[:500] for key, value in metadata.items() if value is not None
            }
        return payload

    def sign_receipt(
        self,
        cash_register_id,
        receipt_id,
        receipt_type,
        amounts_per_vat_rate,
        amounts_per_payment_type,
        line_items,
        metadata=None,
    ):
        """PUT /cash-register/{id}/receipt/{id} - returns synchronously with
        ``receipt_number``, ``time_signature``, ``qr_code_data`` and ``signed``.

        ``signed == False`` means the SCU could not sign; RKSV §17(4) then
        requires the printed receipt to carry "Sicherheitseinrichtung
        ausgefallen".
        """
        payload = self.build_receipt_payload(
            receipt_type,
            amounts_per_vat_rate,
            amounts_per_payment_type,
            line_items,
            metadata=metadata,
        )
        return self._request(
            "PUT", f"/cash-register/{cash_register_id}/receipt/{receipt_id}", json=payload
        )

    def receipt_get(self, cash_register_id, receipt_id):
        return self._request(
            "GET", f"/cash-register/{cash_register_id}/receipt/{receipt_id}"
        )

    def receipt_list(self, cash_register_id):
        return self._request("GET", f"/cash-register/{cash_register_id}/receipt")
