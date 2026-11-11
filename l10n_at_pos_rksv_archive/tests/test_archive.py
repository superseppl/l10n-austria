# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
import base64
import json
import uuid
from unittest import mock

from odoo import fields
from odoo.exceptions import UserError
from odoo.tests.common import TransactionCase, tagged

from odoo.addons.l10n_at_pos_rksv.tests.common import TestAsignCommonMixin

# Fake JWS: only the base64url signature part is decoded by the signing code.
JWS_FAKE = "eyJhbGciOiJFUzI1NiJ9.cGF5bG9hZA.c2lnbmF0dXJl"
RETENTION_YEARS = 7  # BAO § 132: 7 years
START_SEQ, ZERO_SEQ = 1, 2


@tagged("post_install", "-at_install")
class TestArchive(TransactionCase, TestAsignCommonMixin):
    """Start and zero receipts are archived and protected for 7 years."""

    def setUp(self):
        super().setUp()
        self.open_session(
            {
                "name": "Archive Test",
                "asign_enabled": True,
                "asign_pid": "K91",
                "asign_serial_hex": "abc123",
                "asign_key": base64.b64encode(b"0" * 32).decode(),
                "asign_cert": base64.b64encode(b"dummy").decode(),
                "asign_fid": "ATU12345675",
                "asign_user": "test",
                "asign_password": "test",
                "asign_method": "online",
            }
        )

    def _sync_empty_order(self, seq, asign_type=None):
        """Sync a zero-amount order with mocked online signing."""
        date_order = fields.Datetime.to_string(fields.Datetime.now())
        order_data = {
            "uuid": str(uuid.uuid4()),
            "access_token": str(uuid.uuid4()),
            "session_id": self.pos_session.id,
            "name": "/",
            "date_order": date_order,
            "partner_id": None,
            "fiscal_position_id": None,
            "amount_return": 0.0,
            "to_invoice": False,
            "shipping_date": None,
            "asign_state": "u",
            "user_id": self.env.uid,
            "amount_paid": 0.0,
            "amount_total": 0.0,
            "amount_tax": 0.0,
            "sequence_number": seq,
            "payment_ids": [],
            "lines": [],
        }
        if asign_type:
            order_data["asign_type"] = asign_type
        response = mock.Mock()
        response.status_code = 200
        response.json.return_value = {"result": JWS_FAKE}
        with mock.patch("requests.post", return_value=response):
            result = self.env["pos.order"].sync_from_ui([order_data])
        return self.env["pos.order"].browse([r["id"] for r in result["pos.order"]])

    def _attachments(self, order):
        return self.env["ir.attachment"].search(
            [("res_model", "=", "pos.order"), ("res_id", "=", order.id)]
        )

    # Case 1: the signed Startbeleg gets exactly one JSON attachment
    def test_start_receipt_archived(self):
        order = self._sync_empty_order(START_SEQ)
        self.assertEqual(order.asign_type, "s")
        attachment = self._attachments(order)
        self.assertEqual(len(attachment), 1)
        payload = json.loads(attachment.raw)
        self.assertEqual(payload["kind"], "start")
        self.assertEqual(payload["cash_register_id"], "K91")
        self.assertEqual(payload["qrcode"], order.asign_qrcode)

    # Case 2: a later Nullbeleg is archived as kind "null"
    def test_zero_receipt_archived(self):
        self._sync_empty_order(START_SEQ)
        order = self._sync_empty_order(ZERO_SEQ, asign_type="0")
        self.assertEqual(order.asign_type, "0")
        payload = json.loads(self._attachments(order).raw)
        self.assertEqual(payload["kind"], "null")

    # Case 3: archiving twice creates no duplicate (idempotent)
    def test_archive_idempotent(self):
        order = self._sync_empty_order(START_SEQ)
        order._rksv_archive_receipt("start")
        self.assertEqual(len(self._attachments(order)), 1)

    # Case 4: retention ends at 31 Dec of receipt year + 7
    def test_retention_date(self):
        order = self._sync_empty_order(START_SEQ)
        year = fields.Datetime.context_timestamp(order, order.date_order).year
        self.assertEqual(
            self._attachments(order).rksv_retention_until,
            fields.Date.from_string(f"{year + RETENTION_YEARS}-12-31"),
        )

    # Case 5 (invalid): deleting or changing a protected attachment raises
    def test_retention_blocks_unlink_and_write(self):
        attachment = self._attachments(self._sync_empty_order(START_SEQ))
        with self.assertRaises(UserError):
            attachment.unlink()
        with self.assertRaises(UserError):
            attachment.write({"raw": b"tampered"})

    # Case 6 (edge): after the retention date the attachment can be deleted
    def test_retention_expired_allows_unlink(self):
        attachment = self._attachments(self._sync_empty_order(START_SEQ))
        self.env.cr.execute(
            "UPDATE ir_attachment SET rksv_retention_until = '2000-01-01' WHERE id=%s",
            [attachment.id],
        )
        attachment.invalidate_recordset()
        attachment.unlink()
        self.assertFalse(attachment.exists())
