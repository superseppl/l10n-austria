# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
import base64
from unittest import mock

from odoo.exceptions import ValidationError
from odoo.tests.common import TransactionCase

from .common import TestAsignCommonMixin


class TestProviderConfig(TransactionCase, TestAsignCommonMixin):
    """Required fields are checked by the provider of asign_method."""

    def setUp(self):
        super().setUp()
        self.open_session(
            {
                "name": "Config Test",
                "asign_enabled": True,
                "asign_pid": "K93",
                "asign_serial_hex": "abc123",
                "asign_key": base64.b64encode(b"0" * 32).decode(),
                "asign_cert": base64.b64encode(b"dummy").decode(),
                "asign_fid": "ATU12345675",
                "asign_user": "test",
                "asign_password": "test",
                "asign_method": "online",
            }
        )

    # Case 1: "online" runs its provider check, "card" runs the same one
    def test_provider_check_called(self):
        for method in ("online", "card"):
            with mock.patch.object(
                type(self.pos_config), "_asign_check_online_config"
            ) as check:
                self.pos_config.asign_method = method
                self.pos_config._check_asign_config()
            check.assert_called_once_with()

    # Case 2: online without a serial is still rejected (behaviour unchanged)
    def test_online_requires_serial(self):
        self.pos_config.asign_serial_hex = False
        with self.assertRaises(ValidationError):
            self.pos_config._check_asign_config()

    # Case 3: online with a short AES key is still rejected
    def test_online_requires_valid_key(self):
        self.pos_config.asign_key = base64.b64encode(b"short").decode()
        with self.assertRaises(ValidationError):
            self.pos_config._check_asign_config()

    # Case 4: generic fields stay mandatory for every method
    def test_fiscal_id_required(self):
        self.pos_config.asign_fid = False
        with self.assertRaises(ValidationError):
            self.pos_config._check_asign_config()
