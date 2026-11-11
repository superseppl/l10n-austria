# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
import re
from unittest import mock

import requests

from odoo.addons.l10n_at_pos_rksv.tests.common import TestAsignCommonMixin
from odoo.exceptions import UserError, ValidationError
from odoo.tests.common import TransactionCase

from ..models.fiskaly_client import FiskalyApiError, FiskalyClient

# receipt_id pattern fiskaly validates (SIGN AT API, E_FAILED_SCHEMA_VALIDATION)
FISKALY_UUID_RE = re.compile(
    r"^[a-f0-9]{8}-?[a-f0-9]{4}-?4[a-f0-9]{3}-?[89ab][a-f0-9]{3}-?[a-f0-9]{12}$"
)
LAST_NUMBER = 10  # receipt number of the previous signed order
FISKALY_NUMBER = 12  # fiskaly also numbers Startbeleg/close receipts: not 11
QR = "_R1-AT0_K01_12"
REGISTER_ID = "reg-1"
REGISTER_SERIAL = "ser-1"


def _result(**override):
    result = {"receipt_number": str(FISKALY_NUMBER), "qr_code_data": QR, "signed": True}
    result.update(override)
    return result


class TestFiskalyProvider(TransactionCase, TestAsignCommonMixin):
    """Scene: one POS with method fiskaly and one empty paid order; the fiskaly
    API is replaced by a mock, so no network is used."""

    def setUp(self):
        super().setUp()
        self.env.company.write(
            {"l10n_at_fiskaly_api_key": "key", "l10n_at_fiskaly_api_secret": "secret"}
        )
        self.open_session(
            {
                "name": "Fiskaly Test",
                "asign_enabled": True,
                "asign_pid": "K93",
                "asign_fid": "ATU12345675",
                "asign_method": "fiskaly",
                "l10n_at_cash_regid": REGISTER_ID,
                "l10n_at_cash_register_serial": REGISTER_SERIAL,
                "l10n_at_cash_register_state": "initialized",
            }
        )
        self.order = self._order()

    def _order(self, **vals):
        values = {
            "session_id": self.pos_session.id,
            "config_id": self.pos_config.id,
            "company_id": self.env.company.id,
            "pricelist_id": self.pos_config.pricelist_id.id,
            "amount_tax": 0.0,
            "amount_total": 0.0,
            "amount_paid": 0.0,
            "amount_return": 0.0,
        }
        values.update(vals)
        return self.env["pos.order"].create(values)

    def _sign(self, order=None, **kwargs):
        order = order or self.order
        with mock.patch.object(FiskalyClient, "sign_receipt", **kwargs) as sign:
            return order._asign_sign_fiskaly(self.env["pos.order"]), sign

    # Case 1: the receipt id is stable per order and type (retry-safe)
    def test_receipt_uuid_stable(self):
        normal = self.order._fiskaly_receipt_uuid("NORMAL")
        self.assertEqual(normal, self.order._fiskaly_receipt_uuid("NORMAL"))
        self.assertNotEqual(normal, self.order._fiskaly_receipt_uuid("CANCELLATION"))
        self.assertNotEqual(normal, self._order()._fiskaly_receipt_uuid("NORMAL"))
        self.assertRegex(normal, FISKALY_UUID_RE)

    # Case 2: a signed receipt returns fiskaly's number, QR and no outage flag
    def test_signed(self):
        data, sign = self._sign(return_value=_result())
        self.assertEqual(data["asign_state"], "s")
        self.assertEqual(data["asign_seq"], FISKALY_NUMBER)
        self.assertEqual(data["asign_qrcode"], QR)
        self.assertFalse(data["fiskaly_outage"])
        self.assertEqual(
            sign.call_args.kwargs["receipt_id"], self.order._fiskaly_receipt_uuid("NORMAL")
        )

    # Case 3: signed false -> numbered, kept as signed state (no retry), outage flag
    def test_unsigned_by_fiskaly_is_outage(self):
        data, _sign = self._sign(return_value=_result(signed=False))
        self.assertEqual(data["asign_state"], "s")
        self.assertTrue(data["fiskaly_outage"])

    # Case 4: a Storno (asign_type c, as set for refunds) is sent as CANCELLATION.
    # refunded_order_id is computed from order lines in Odoo 19, so the type is
    # set directly instead of building a refund with product lines.
    def test_refund_is_cancellation(self):
        refund = self._order(asign_type="c")
        data, sign = self._sign(refund, return_value=_result())
        self.assertEqual(sign.call_args.kwargs["receipt_type"], "CANCELLATION")
        self.assertEqual(data["asign_type"], "c")

    # Case 5 (invalid): fiskaly refuses -> UserError, nothing is written
    def test_api_error_raises_user_error(self):
        with self.assertRaises(UserError):
            self._sign(side_effect=FiskalyApiError("boom", status_code=400))

    # Case 6 (invalid): register not initialized -> UserError before any call
    def test_uninitialized_register(self):
        self.pos_config.l10n_at_cash_register_state = "registered"
        with mock.patch.object(FiskalyClient, "sign_receipt") as sign:
            with self.assertRaises(UserError):
                self.order._asign_sign_fiskaly(self.env["pos.order"])
        sign.assert_not_called()

    # Case 7: timeout -> order stays unsigned and is signed by the next run
    def test_timeout_keeps_order_unsigned(self):
        self.order.write({"asign_state": "u", "state": "paid"})
        with mock.patch.object(
            FiskalyClient, "sign_receipt", side_effect=requests.exceptions.Timeout
        ):
            self.order._asign_add_signature()
        self.assertEqual(self.order.asign_state, "u")
        with mock.patch.object(FiskalyClient, "sign_receipt", return_value=_result()):
            self.order._asign_add_signature()
        self.assertEqual(self.order.asign_state, "s")

    # Case 8: fiskaly's number is taken even though it is not last + 1
    def test_core_flow_takes_fiskaly_number(self):
        self.order.write(
            {"asign_state": "s", "asign_seq": LAST_NUMBER, "state": "paid"}
        )
        new = self._order()
        new.write({"asign_state": "u", "state": "paid"})
        with mock.patch.object(FiskalyClient, "sign_receipt", return_value=_result()):
            new._asign_add_signature()
        self.assertEqual(new.asign_state, "s")
        self.assertEqual(new.asign_seq, FISKALY_NUMBER)
        self.assertEqual(new.asign_qrcode, QR)

    # Case 9 (invalid): method fiskaly without API credentials is refused
    def test_config_needs_credentials(self):
        self.env.company.l10n_at_fiskaly_api_key = False
        with self.assertRaises(ValidationError):
            self.pos_config._check_asign_config()


class TestFiskalySettings(TransactionCase):
    """The settings wizard accepts method fiskaly without serial and key."""

    # Case 10: the form loads and a POS can be switched to fiskaly and saved
    def test_settings_save_with_fiskaly(self):
        self.env.company.write(
            {"l10n_at_fiskaly_api_key": "key", "l10n_at_fiskaly_api_secret": "secret"}
        )
        config = self.env["pos.config"].create({"name": "Fiskaly Settings"})
        settings = self.env["res.config.settings"].create({"pos_config_id": config.id})
        self.env["res.config.settings"].get_views([(False, "form")])
        settings.write(
            {
                "asign_enabled": True,
                "asign_method": "fiskaly",
                "asign_fid": "ATU12345675",
            }
        )
        settings.execute()
        self.assertEqual(config.asign_method, "fiskaly")
        self.assertTrue(config.asign_enabled)
