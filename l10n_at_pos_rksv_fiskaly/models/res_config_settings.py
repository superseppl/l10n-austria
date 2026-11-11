# Copyright 2026 Sebastian Bader
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    l10n_at_fiskaly_api_key = fields.Char(related="company_id.l10n_at_fiskaly_api_key", readonly=False)
    l10n_at_fiskaly_api_secret = fields.Char(related="company_id.l10n_at_fiskaly_api_secret", readonly=False)
    l10n_at_fiskaly_organization_id = fields.Char(related="company_id.l10n_at_fiskaly_organization_id", readonly=False)
    l10n_at_fiskaly_environment = fields.Char(related="company_id.l10n_at_fiskaly_environment", readonly=True)
    l10n_at_pos_company_scuid = fields.Char(related="company_id.l10n_at_pos_company_scuid", readonly=True)
    l10n_at_fiskaly_scu_state = fields.Selection(
        related="company_id.l10n_at_fiskaly_scu_state", readonly=True
    )
    l10n_at_fon_participant_id = fields.Char(related="company_id.l10n_at_fon_participant_id", readonly=False)
    l10n_at_fon_user_id = fields.Char(related="company_id.l10n_at_fon_user_id", readonly=False)
    l10n_at_fon_user_pin = fields.Char(related="company_id.l10n_at_fon_user_pin", readonly=False)
    l10n_at_is_fon_authenticated = fields.Boolean(related="company_id.l10n_at_is_fon_authenticated")

    l10n_at_cash_regid = fields.Char(related="pos_config_id.l10n_at_cash_regid", readonly=True)
    l10n_at_cash_register_serial = fields.Char(
        related="pos_config_id.l10n_at_cash_register_serial", readonly=True
    )
    l10n_at_cash_register_state = fields.Selection(
        related="pos_config_id.l10n_at_cash_register_state", readonly=True
    )

    def _l10n_at_notify(self, message, notification_type="success"):
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": "RKSV Austria (fiskaly)",
                "message": message,
                "type": notification_type,
                "sticky": False,
            },
        }

    def action_l10n_at_fiskaly_authenticate(self):
        self.ensure_one()
        self.company_id.action_fiskaly_authenticate()
        environment = self.company_id.l10n_at_fiskaly_environment or "unknown"
        message = f"Authenticated. Environment of this API key: {environment}."
        if environment != "LIVE":
            message += " Only LIVE keys produce legally binding receipts."
            return self._l10n_at_notify(message, "warning")
        return self._l10n_at_notify(message)

    def action_l10n_at_fiskaly_provision_scu(self):
        self.ensure_one()
        self.company_id.action_fiskaly_provision_scu()
        state = dict(
            self.company_id._fields["l10n_at_fiskaly_scu_state"].selection
        ).get(self.company_id.l10n_at_fiskaly_scu_state, "unknown")
        return self._l10n_at_notify(f"Signature Creation Unit: {state}.")

    def action_l10n_at_fiskaly_init_scu(self):
        self.ensure_one()
        self.company_id.action_fiskaly_initialize_scu()
        return self._l10n_at_notify("Signature Creation Unit initialized - it is now registered with FinanzOnline.")

    def action_l10n_at_fiskaly_fon_auth(self):
        self.ensure_one()
        self.company_id.action_fiskaly_fon_auth()
        return self._l10n_at_notify("FinanzOnline linked. You can now initialize the Signature Creation Unit.")

    def action_l10n_at_fiskaly_init_cash_register(self):
        self.ensure_one()
        self.pos_config_id.action_fiskaly_initialize_cash_register()
        return self._l10n_at_notify("Cash register created, registered and initialized.")
