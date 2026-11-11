# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
from odoo import fields, models
from odoo.exceptions import UserError


class IrAttachment(models.Model):
    _inherit = "ir.attachment"

    rksv_retention_until = fields.Date(
        string="RKSV Retention Until",
        index="btree_not_null",
        readonly=True,
        help="Legal retention end (RKSV / BAO). Deleting before is blocked.",
    )

    def _rksv_check_retention(self):
        today = fields.Date.context_today(self)
        locked = self.filtered(
            lambda a: a.rksv_retention_until and a.rksv_retention_until >= today
        )
        if locked:
            raise UserError(
                self.env._(
                    "Attachment %(name)s must be kept until %(date)s (RKSV).",
                    name=locked[0].name,
                    date=locked[0].rksv_retention_until,
                )
            )

    def unlink(self):
        self._rksv_check_retention()
        return super().unlink()

    def write(self, vals):
        if {"datas", "raw", "db_datas", "name", "res_id", "res_model"} & vals.keys():
            self._rksv_check_retention()
        return super().write(vals)
