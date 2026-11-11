# Copyright 2026 Sebastian Bader
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
{
    "name": "POS Austria RKSV - fiskaly provider",
    "summary": "Sign RKSV receipts through fiskaly SIGN AT instead of a.sign",
    "author": "Sebastian Bader, Odoo Community Association (OCA)",
    "website": "https://github.com/OCA/l10n-austria",
    "category": "Localization/Austria",
    "version": "19.0.1.0.0",
    "license": "LGPL-3",
    "depends": ["l10n_at_pos_rksv"],
    "external_dependencies": {"python": ["requests"]},
    "data": [],
    "development_status": "Alpha",
    "installable": True,
}
