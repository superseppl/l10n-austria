# Copyright 2026 Sebastian Bader
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
import re
import uuid

from odoo import fields, models
from odoo.exceptions import UserError

from .fiskaly_client import FiskalyApiError, FiskalyClient

# fiskaly only accepts an Austrian UID for a vat_id legal entity:
# "ATU" followed by exactly eight digits.
AT_VAT_ID_RE = re.compile(r"^ATU\d{8}$")

SCU_STATES = [
    ("none", "Not created"),
    ("pending", "Pending"),
    ("created", "Created"),
    ("initialized", "Initialized"),
    ("decommissioned", "Decommissioned"),
    ("outage", "Outage"),
    ("defective", "Defective"),
]


def _fiskaly_user_message(error):
    """Turn an API error into something an accountant can act on."""
    message = error.detail or str(error)
    if error.code == "E_MISSING_FON_CREDENTIALS":
        message += (
            "\n\nFinanzOnline is not linked for this organization yet. Create a "
            "dedicated 'Registrierkassen-Webservice-Benutzer' in FinanzOnline "
            "(not your personal login), enter its Teilnehmer-ID, Benutzer-ID "
            "and PIN below, then click 'Link FinanzOnline'."
        )
    elif error.code == "E_INVALID_FON_CREDENTIALS":
        message += (
            "\n\nFinanzOnline rejected the credentials. Check that the user is a "
            "Cash Register Webservice User and that the PIN is current."
        )
    return message


class ResCompany(models.Model):
    _inherit = "res.company"

    # Field names deliberately match the columns already present on
    # res_company in the Enterprise (l10n_at_pos) schema, so a migration
    # from Enterprise simply reuses the existing columns instead of moving
    # data between differently-named fields.
    #
    # l10n_at_pos_test_mode is deliberately NOT carried over: SIGN AT serves
    # TEST and LIVE from the same host and derives the environment from the
    # API key, so a switch would be a lie. l10n_at_fiskaly_environment shows
    # what fiskaly actually reported for the configured key instead.
    l10n_at_fiskaly_api_key = fields.Char(string="fiskaly API Key", groups="base.group_system")
    l10n_at_fiskaly_api_secret = fields.Char(string="fiskaly API Secret", groups="base.group_system")
    l10n_at_fiskaly_access_token = fields.Char(string="fiskaly Access Token", groups="base.group_system")
    l10n_at_fiskaly_organization_id = fields.Char(string="fiskaly Organization ID")
    l10n_at_fiskaly_environment = fields.Char(
        string="fiskaly Environment",
        readonly=True,
        help="TEST or LIVE, as reported by fiskaly for the configured API key. "
        "Only LIVE produces legally binding signatures.",
    )
    l10n_at_fon_participant_id = fields.Char(string="FinanzOnline Participant ID")
    l10n_at_fon_user_id = fields.Char(string="FinanzOnline User ID")
    l10n_at_fon_user_pin = fields.Char(string="FinanzOnline User PIN", groups="base.group_system")
    l10n_at_pos_company_scuid = fields.Char(string="Signature Creation Unit ID", readonly=True)
    l10n_at_fiskaly_scu_state = fields.Selection(
        SCU_STATES, string="Signature Creation Unit State", default="none", readonly=True
    )
    l10n_at_pos_is_tax_exempted = fields.Boolean(string="Tax Exempted (Kleinunternehmer)")
    l10n_at_is_odoo_managed_org = fields.Boolean(
        string="Organization provisioned via Odoo (legacy)",
        help="Informational only, carried over from an Enterprise migration. "
        "This module always talks to fiskaly directly with the API key/secret "
        "configured above; it does not manage organizations through Odoo.",
    )
    l10n_at_is_fon_authenticated = fields.Boolean(string="FinanzOnline Authenticated", readonly=True)

    # -- plumbing ---------------------------------------------------------
    def _get_fiskaly_client(self):
        self.ensure_one()
        if not (self.l10n_at_fiskaly_api_key and self.l10n_at_fiskaly_api_secret):
            raise UserError(
                "Configure the fiskaly API Key and API Secret on the company "
                "before using RKSV signing."
            )
        return FiskalyClient(
            api_key=self.l10n_at_fiskaly_api_key,
            api_secret=self.l10n_at_fiskaly_api_secret,
            access_token=self.l10n_at_fiskaly_access_token,
        )

    def _fiskaly_run(self, method, *args, **kwargs):
        """Call a client method and convert API failures into a UserError that
        names the actual problem instead of surfacing a raw HTTP 400."""
        try:
            return method(*args, **kwargs)
        except FiskalyApiError as error:
            raise UserError(_fiskaly_user_message(error)) from error

    def _fiskaly_store_auth(self, client):
        self.write({
            "l10n_at_fiskaly_access_token": client.access_token,
            "l10n_at_fiskaly_environment": client.environment,
            "l10n_at_fiskaly_organization_id": (
                client.organization_id or self.l10n_at_fiskaly_organization_id
            ),
        })

    def _fiskaly_apply_scu_state(self, result):
        state = (result or {}).get("state")
        known = dict(SCU_STATES)
        normalised = state.lower() if state else "none"
        self.l10n_at_fiskaly_scu_state = normalised if normalised in known else "none"

    def _fiskaly_new_scu_id(self):
        return str(uuid.uuid4())

    # -- actions -----------------------------------------------------------
    def action_fiskaly_authenticate(self):
        """Exchange the API key/secret for an access token and store it, along
        with the environment fiskaly reports for that key."""
        for company in self:
            client = company._get_fiskaly_client()
            company._fiskaly_run(client.authenticate)
            company._fiskaly_store_auth(client)
        return True

    def action_fiskaly_fon_auth(self):
        """Link the fiskaly organization to FinanzOnline."""
        for company in self:
            if not (
                company.l10n_at_fon_participant_id
                and company.l10n_at_fon_user_id
                and company.l10n_at_fon_user_pin
            ):
                raise UserError(
                    "Configure the FinanzOnline Participant ID, User ID and "
                    "User PIN (from a dedicated 'Cash Register Webservice "
                    "User' in FinanzOnline, not a personal login) first."
                )
            client = company._get_fiskaly_client()
            company._fiskaly_run(client.authenticate)
            company._fiskaly_store_auth(client)
            company._fiskaly_run(
                client.fon_auth,
                company.l10n_at_fon_participant_id,
                company.l10n_at_fon_user_id,
                company.l10n_at_fon_user_pin,
            )
            company.l10n_at_is_fon_authenticated = True
        return True

    def action_fiskaly_provision_scu(self):
        """Create the company's Signature Creation Unit, or adopt the one whose
        ID is already stored (idempotent, so it is safe to click twice)."""
        self.ensure_one()
        company = self
        vat_id = (company.vat or "").replace(" ", "").upper()
        if not AT_VAT_ID_RE.match(vat_id):
            raise UserError(
                "The company's VAT ID must be an Austrian UID of the form "
                f"ATU12345678 (found: {company.vat or 'empty'}). fiskaly uses it "
                "as the legal entity of the Signature Creation Unit."
            )
        client = company._get_fiskaly_client()
        company._fiskaly_run(client.authenticate)
        company._fiskaly_store_auth(client)

        if company.l10n_at_pos_company_scuid:
            result = company._fiskaly_run(client.scu_get, company.l10n_at_pos_company_scuid)
        else:
            scu_id = company._fiskaly_new_scu_id()
            result = company._fiskaly_run(
                client.scu_create,
                scu_id,
                vat_id=vat_id,
                legal_entity_name=company.name,
            )
            company.l10n_at_pos_company_scuid = scu_id
        company._fiskaly_apply_scu_state(result)
        return True

    def action_fiskaly_initialize_scu(self):
        """Transition the SCU to INITIALIZED - the step that registers it with
        FinanzOnline. Requires a successful 'Link FinanzOnline' first."""
        self.ensure_one()
        if not self.l10n_at_pos_company_scuid:
            raise UserError("Create the Signature Creation Unit first.")
        client = self._get_fiskaly_client()
        self._fiskaly_run(client.authenticate)
        self._fiskaly_store_auth(client)
        result = self._fiskaly_run(client.scu_initialize, self.l10n_at_pos_company_scuid)
        self._fiskaly_apply_scu_state(result)
        return True

    def action_fiskaly_refresh_scu(self):
        self.ensure_one()
        if not self.l10n_at_pos_company_scuid:
            raise UserError("Create the Signature Creation Unit first.")
        client = self._get_fiskaly_client()
        self._fiskaly_run(client.authenticate)
        self._fiskaly_store_auth(client)
        result = self._fiskaly_run(client.scu_get, self.l10n_at_pos_company_scuid)
        self._fiskaly_apply_scu_state(result)
        return True
