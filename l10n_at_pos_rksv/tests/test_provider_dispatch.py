# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
import base64
from unittest import mock

from odoo.exceptions import UserError
from odoo.tests.common import TransactionCase

from .common import TestAsignCommonMixin

SENTINEL = {"asign_state": "s"}


class TestProviderDispatch(TransactionCase, TestAsignCommonMixin):
    """_asign_create_signature hands over to _asign_sign_<method>."""

    def setUp(self):
        super().setUp()
        self.open_session(
            {
                "name": "Dispatch Test",
                "asign_enabled": True,
                "asign_pid": "K92",
                "asign_serial_hex": "abc123",
                "asign_key": base64.b64encode(b"0" * 32).decode(),
                "asign_cert": base64.b64encode(b"dummy").decode(),
                "asign_fid": "ATU12345675",
                "asign_user": "test",
                "asign_password": "test",
                "asign_method": "online",
            }
        )
        # One unsigned order is enough: only the dispatch is under test.
        self.order = self.env["pos.order"].create(
            {
                "session_id": self.pos_session.id,
                "config_id": self.pos_config.id,
                "company_id": self.env.company.id,
                "pricelist_id": self.pos_config.pricelist_id.id,
                "amount_tax": 0.0,
                "amount_total": 0.0,
                "amount_paid": 0.0,
                "amount_return": 0.0,
            }
        )

    # Case 1: method "online" calls _asign_sign_online and returns its result
    def test_online_dispatched(self):
        with mock.patch.object(
            type(self.order), "_asign_sign_online", return_value=SENTINEL
        ) as handler:
            result = self.order._asign_create_signature(self.env["pos.order"])
        handler.assert_called_once()
        self.assertEqual(result, SENTINEL)

    # Case 2: "card" keeps the old behaviour (same online signing path)
    def test_card_uses_online_path(self):
        self.pos_config.asign_method = "card"
        with mock.patch.object(
            type(self.order), "_asign_sign_online", return_value=SENTINEL
        ) as handler:
            result = self.order._asign_create_signature(self.env["pos.order"])
        handler.assert_called_once()
        self.assertEqual(result, SENTINEL)

    # Case 3 (invalid): a method without a handler raises a clear error
    def test_unknown_method_raises(self):
        self.pos_config.write({"asign_method": False})
        with self.assertRaises(UserError):
            self.order._asign_create_signature(self.env["pos.order"])
