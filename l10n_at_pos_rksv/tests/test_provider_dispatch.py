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


class TestExternalNumbering(TestProviderDispatch):
    """A provider that numbers receipts itself is not held to the local gap check."""

    FISKALY_NUMBER = 12  # not last + 1: Startbeleg and close receipts used numbers
    LAST_NUMBER = 10

    def _result(self):
        return {
            "asign_state": "s",
            "asign_seq": self.FISKALY_NUMBER,
            "asign_qrcode": "qr",
            "asign_serial": "ser",
            "asign_counter": "0",
            "asign_type": "o",
            "asign_dep": "dep",
        }

    def _two_orders(self):
        last = self.order
        last.write(
            {"asign_state": "s", "asign_seq": self.LAST_NUMBER, "state": "paid"}
        )
        new = last.copy({"asign_state": "u", "state": "paid"})
        new.write({"asign_state": "u", "state": "paid"})
        return new

    # Case 4: local numbering keeps the gap check (provider number 12 != 10 + 1)
    def test_local_numbering_aborts_on_gap(self):
        new = self._two_orders()
        with mock.patch.object(
            type(new), "_asign_sign_online", return_value=self._result()
        ):
            new._asign_add_signature()
        self.assertEqual(new.asign_state, "u")

    # Case 5: external numbering accepts the provider number and stores it
    def test_external_numbering_takes_provider_number(self):
        new = self._two_orders()
        with (
            mock.patch.object(
                type(self.pos_config), "_asign_external_numbering", return_value=True
            ),
            mock.patch.object(
                type(new), "_asign_sign_online", return_value=self._result()
            ),
        ):
            new._asign_add_signature()
        self.assertEqual(new.asign_state, "s")
        self.assertEqual(new.asign_seq, self.FISKALY_NUMBER)
