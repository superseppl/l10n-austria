# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
import json

from odoo import fields, models

RETENTION_YEARS = 7
KIND_BY_TYPE = {"s": "start", "0": "null"}


class PosOrder(models.Model):
    _inherit = "pos.order"

    def _rksv_retention_until(self):
        """End of the calendar year RETENTION_YEARS after the receipt."""
        self.ensure_one()
        year = fields.Datetime.context_timestamp(self, self.date_order).year
        return fields.Date.from_string(f"{year + RETENTION_YEARS}-12-31")

    def _rksv_archive_receipt(self, kind):
        """Attach the signed receipt as JSON; idempotent per order and kind."""
        self.ensure_one()
        name = f"RKSV-{kind}-{self.config_id.asign_pid}-{self.asign_seq}.json"
        existing = self.env["ir.attachment"].search_count(
            [
                ("res_model", "=", "pos.order"),
                ("res_id", "=", self.id),
                ("name", "=", name),
            ]
        )
        if existing:
            return self.env["ir.attachment"]
        payload = {
            "kind": kind,
            "name": self.name,
            "date_order": fields.Datetime.to_string(self.date_order),
            "pos": self.config_id.name,
            "cash_register_id": self.config_id.asign_pid,
            "serial": self.asign_serial,
            "type": self.asign_type,
            "sequence": self.asign_seq,
            "counter": self.asign_counter,
            "qrcode": self.asign_qrcode,
            "dep": self.asign_dep,
        }
        return self.env["ir.attachment"].create(
            {
                "name": name,
                "res_model": "pos.order",
                "res_id": self.id,
                "mimetype": "application/json",
                "raw": json.dumps(payload, indent=2).encode(),
                "rksv_retention_until": self._rksv_retention_until(),
            }
        )

    def _asign_add_signature(self, *args, **kwargs):
        signed = super()._asign_add_signature(*args, **kwargs)
        for order in signed or self.browse():
            kind = KIND_BY_TYPE.get(order.asign_type)
            if kind and order.asign_state == "s":
                order._rksv_archive_receipt(self.env.context.get("rksv_kind", kind))
        return signed
