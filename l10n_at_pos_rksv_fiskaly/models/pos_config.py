# Copyright 2026 Sebastian Bader
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
from odoo import fields, models
from odoo.exceptions import UserError, ValidationError

from .fiskaly_client import FiskalyApiError


class PosConfig(models.Model):
    _inherit = "pos.config"

    # Same field name as the Enterprise l10n_at_pos schema.
    l10n_at_cash_regid = fields.Char(
        string="fiskaly Cash Register ID",
        help="Identifier of the fiskaly cash-register resource bound to this "
        "POS. Generated automatically the first time the register is "
        "initialized; one fiskaly cash register per physical POS register.",
    )
    l10n_at_cash_register_serial = fields.Char(
        string="fiskaly Cash Register Serial",
        readonly=True,
        help="Serial number assigned by fiskaly when the register was created.",
    )
    l10n_at_cash_register_state = fields.Selection(
        [
            ("none", "Not created"),
            ("created", "Created"),
            ("registered", "Registered"),
            ("initialized", "Initialized"),
            ("decommissioned", "Decommissioned"),
        ],
        string="fiskaly Cash Register State",
        default="none",
        readonly=True,
    )

    asign_method = fields.Selection(selection_add=[("fiskaly", "fiskaly")], ondelete={"fiskaly": "set default"})

    def _asign_external_numbering(self):
        """fiskaly numbers the receipts (Startbeleg, month and year receipts
        use numbers too), so the local gapless sequence is not authoritative."""
        self.ensure_one()
        return self.asign_method == "fiskaly" or super()._asign_external_numbering()

    def _asign_dep_export(self):
        """The DEP of a fiskaly register lives at fiskaly: the receipts' own
        asign_dep stays empty, so the core's local export would be empty."""
        self.ensure_one()
        if self.asign_method != "fiskaly":
            return super()._asign_dep_export()
        company = self.company_id
        if not self.l10n_at_cash_regid:
            raise UserError(
                self.env._("POS %s has no fiskaly cash register yet.", self.name)
            )
        client = company._get_fiskaly_client()
        return company._fiskaly_run(client.cash_register_export, self.l10n_at_cash_regid)

    def _asign_check_fiskaly_config(self):
        """No certificate and no local AES key: the SCU at fiskaly holds them.
        The register may still be uninitialized here (it is initialized from
        this very configuration); signing checks that."""
        self.ensure_one()
        company = self.company_id
        if not (company.l10n_at_fiskaly_api_key and company.l10n_at_fiskaly_api_secret):
            raise ValidationError(
                self.env._(
                    "Method fiskaly needs the fiskaly API key and secret on the "
                    "company (Settings > Point of Sale)."
                )
            )

    def _l10n_at_require_scu(self):
        """Every cash register signs through the company's SCU, so the register
        cannot be initialized before the SCU is."""
        self.ensure_one()
        company = self.company_id
        if company.l10n_at_fiskaly_scu_state == "initialized":
            return company
        state = dict(
            company._fields["l10n_at_fiskaly_scu_state"].selection
        ).get(company.l10n_at_fiskaly_scu_state or "none", "Not created")
        raise UserError(
            "The company's Signature Creation Unit is '%s'. Create and "
            "initialize it under Settings > Point of Sale > RKSV Austria "
            "before initializing a cash register: every register signs "
            "through the SCU." % state
        )

    def action_fiskaly_initialize_cash_register(self):
        self.ensure_one()
        if not (self.asign_enabled and self.asign_method == "fiskaly"):
            raise UserError("Enable RKSV on this POS and choose the method 'fiskaly' first.")
        company = self._l10n_at_require_scu()
        client = company._get_fiskaly_client()
        if not self.l10n_at_cash_regid:
            self.l10n_at_cash_regid = company._fiskaly_new_scu_id()
        if self.l10n_at_cash_register_state == "none":
            # The register may already exist in fiskaly - e.g. it was created
            # from a script, or a previous run created it and failed before
            # the state was written. PUT would then fail, so look first.
            existing = None
            try:
                existing = client.cash_register_get(self.l10n_at_cash_regid)
            except FiskalyApiError as error:
                if error.status_code != 404:
                    raise UserError(error.detail or str(error)) from error
            if existing:
                self._l10n_at_apply_cash_register_state(existing)
            else:
                result = company._fiskaly_run(
                    client.cash_register_create, self.l10n_at_cash_regid, self.display_name
                )
                self._l10n_at_apply_cash_register_state(result)
        if self.l10n_at_cash_register_state == "created":
            company._fiskaly_run(client.cash_register_register, self.l10n_at_cash_regid)
            self.l10n_at_cash_register_state = "registered"
        if self.l10n_at_cash_register_state == "registered":
            company._fiskaly_run(client.cash_register_initialize, self.l10n_at_cash_regid)
            self.l10n_at_cash_register_state = "initialized"
        return True

    def _l10n_at_apply_cash_register_state(self, result):
        """Mirror the state fiskaly reports onto the POS configuration."""
        known = dict(self._fields["l10n_at_cash_register_state"].selection)
        state = ((result or {}).get("state") or "none").lower()
        self.l10n_at_cash_register_state = state if state in known else "none"
        if (result or {}).get("serial_number"):
            self.l10n_at_cash_register_serial = result["serial_number"]

    def _load_pos_data_fields(self, config):
        res = super()._load_pos_data_fields(config)
        if not res:
            return res
        res += [
            "l10n_at_cash_register_state",
        ]
        return res

    def action_fiskaly_decommission_cash_register(self):
        """Required before retiring a register for good (e.g. moving to a
        different provider/SCU) - produces the legally required DECOMMISSION
        receipt."""
        self.ensure_one()
        if self.l10n_at_cash_register_state != "initialized":
            raise UserError("Only an initialized cash register can be decommissioned.")
        client = self.company_id._get_fiskaly_client()
        self.company_id._fiskaly_run(client.cash_register_decommission, self.l10n_at_cash_regid)
        self.l10n_at_cash_register_state = "decommissioned"
        return True
