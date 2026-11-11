# Copyright 2026 Sebastian Bader
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
import logging
import uuid
from collections import defaultdict

from odoo import fields, models
from odoo.exceptions import UserError

from .fiskaly_client import FiskalyApiError


_logger = logging.getLogger(__name__)

# account.tax.amount_type == 'percent' -> amount is the VAT percentage.
# Map the Austrian standard rates to fiskaly's vat_rate enum. Grubigstube uses
# 20% (Speisen/Getränke) and 10% (Speisen ausser Haus / Beherbergung); 13% is
# the Jungholz/Mittelberg rate and 19% the Steuerbefreiung bucket.
VAT_RATE_MAP = {
    20.0: "STANDARD",
    10.0: "REDUCED_1",
    13.0: "REDUCED_2",
    19.0: "SPECIAL",
    0.0: "ZERO",
}


class PosOrder(models.Model):
    _inherit = "pos.order"

    fiskaly_outage = fields.Boolean(
        string="fiskaly Outage Receipt",
        readonly=True,
        copy=False,
        help="fiskaly numbered the receipt but its SCU could not sign it. RKSV "
        "§17(4): print 'Sicherheitseinrichtung ausgefallen'. It is not retried, "
        "because a second signing attempt would use a second receipt number.",
    )
    fiskaly_receipt_id = fields.Char(string="fiskaly Receipt ID", readonly=True, copy=False)

    # -- data shaping ------------------------------------------------------
    def _l10n_at_fiskaly_amounts_per_vat_rate(self, sign=1):
        self.ensure_one()
        totals = defaultdict(float)
        for line in self.lines:
            taxes = line.tax_ids.filtered(lambda t: t.amount_type == "percent")
            rate = round(taxes[0].amount, 2) if taxes else 0.0
            container = VAT_RATE_MAP.get(rate)
            if container is None:
                # Silently booking an unknown rate as ZERO would understate the
                # VAT container on a legally binding receipt - say so loudly.
                _logger.warning(
                    "RKSV: VAT rate %s%% on order %s has no fiskaly container "
                    "mapping; booking it as ZERO. Extend VAT_RATE_MAP.",
                    rate,
                    self.pos_reference,
                )
                container = "ZERO"
            totals[container] += line.price_subtotal_incl * sign
        return [{"vat_rate": key, "amount": amount} for key, amount in totals.items()] or [
            {"vat_rate": "ZERO", "amount": 0.0}
        ]

    def _l10n_at_fiskaly_amounts_per_payment_type(self, sign=1):
        self.ensure_one()
        totals = defaultdict(float)
        for payment in self.payment_ids:
            key = "CASH" if payment.payment_method_id.is_cash_count else "NON_CASH"
            totals[key] += payment.amount * sign
        return [{"payment_type": key, "amount": amount} for key, amount in totals.items()] or [
            {"payment_type": "NON_CASH", "amount": 0.0}
        ]

    def _l10n_at_fiskaly_line_items(self, sign=1):
        """standard_v1 requires line_items; they carry the same gross figures
        the VAT containers summarise."""
        self.ensure_one()
        items = []
        for line in self.lines:
            gross_unit_price = (line.price_subtotal_incl / line.qty) if line.qty else 0.0
            items.append({
                "quantity": line.qty * sign,
                "text": line.product_id.display_name or "Position",
                "price_per_unit": gross_unit_price,
            })
        return items or [{"quantity": 0, "text": "Leerbeleg", "price_per_unit": 0.0}]

    @staticmethod
    def _l10n_at_fiskaly_null_receipt_data():
        """A Nullbeleg: a real, numbered receipt whose amounts are all zero.
        Used for orders that were cancelled before ever being paid, so the
        receipt sequence stays gapless without inventing revenue."""
        return (
            [{"vat_rate": "ZERO", "amount": 0.0}],
            [{"payment_type": "NON_CASH", "amount": 0.0}],
            [{"quantity": 0, "text": "Nullbeleg", "price_per_unit": 0.0}],
        )

    # -- signing -----------------------------------------------------------
    def _fiskaly_receipt_uuid(self, receipt_type):
        """Same order and type always give the same id.

        A retry after a timeout or outage then PUTs the same receipt instead of
        creating a second one (fiscalizing a sale twice). Derived, not stored:
        the core rolls back writes of a failed signing attempt.
        """
        self.ensure_one()
        key = f"{self.config_id.l10n_at_cash_regid}/{self.id}/{receipt_type}"
        return str(uuid.uuid5(uuid.NAMESPACE_URL, key))

    def _asign_sign_fiskaly(self, last_order):
        """Provider hook of l10n_at_pos_rksv: sign through the fiskaly SIGN AT API.

        Returns the dict the core writes on the order. Raises UserError when
        the receipt could not be handed to fiskaly at all (the order then stays
        unsigned and is retried).
        """
        self.ensure_one()
        config = self.config_id
        if not config.l10n_at_cash_regid or config.l10n_at_cash_register_state != "initialized":
            raise UserError(
                self.env._(
                    "POS %s has no initialized fiskaly cash register.", config.display_name
                )
            )
        if self.refunded_order_id or self.asign_type == "c":
            receipt_type, asign_type = "CANCELLATION", "c"
        else:
            receipt_type, asign_type = "NORMAL", "o"
        # Refund orders already carry negative lines and payments: sign=1.
        amounts_vat = self._l10n_at_fiskaly_amounts_per_vat_rate()
        amounts_payment = self._l10n_at_fiskaly_amounts_per_payment_type()
        line_items = self._l10n_at_fiskaly_line_items()
        if asign_type == "o" and not self.amount_total:
            asign_type = "0"
        receipt_id = self._fiskaly_receipt_uuid(receipt_type)
        client = self.company_id._get_fiskaly_client()
        try:
            result = client.sign_receipt(
                cash_register_id=config.l10n_at_cash_regid,
                receipt_id=receipt_id,
                receipt_type=receipt_type,
                amounts_per_vat_rate=amounts_vat,
                amounts_per_payment_type=amounts_payment,
                line_items=line_items,
                metadata={"pos_reference": self.pos_reference or ""},
            )
        except FiskalyApiError as error:
            raise UserError(error.detail or str(error)) from error
        # A timeout raises requests' RequestException, which the core catches
        # and treats like UserError: the order stays unsigned.
        number = int(result["receipt_number"])
        signed = bool(result.get("signed", True))
        if not signed:
            _logger.error(
                "fiskaly returned an UNSIGNED receipt for order %s: %s",
                self.pos_reference,
                result.get("hints"),
            )
        return {
            "asign_state": "s",
            "asign_seq": number,
            "name": config.order_seq_id.get_next_char(number),
            "asign_type": asign_type,
            "asign_qrcode": result.get("qr_code_data") or "",
            "asign_serial": config.l10n_at_cash_register_serial or "",
            "asign_counter": "",
            "asign_dep": "",
            "fiskaly_receipt_id": receipt_id,
            "fiskaly_outage": not signed,
        }

    def _load_pos_data_fields(self, config):
        res = super()._load_pos_data_fields(config)
        if not res:
            return res
        return res + ["fiskaly_outage"]
