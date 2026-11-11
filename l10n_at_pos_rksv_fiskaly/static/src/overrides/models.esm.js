// License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).

import {PosOrder} from "@point_of_sale/app/models/pos_order";
import {patch} from "@web/core/utils/patch";

patch(PosOrder.prototype, {
    /**
     * @override - fiskaly numbered the receipt but could not sign it (RKSV §17(4))
     */
    setup(vals) {
        super.setup(vals);
        this.fiskaly_outage = vals.fiskaly_outage || false;
    },
});
