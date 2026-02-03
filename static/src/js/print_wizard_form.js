/** @odoo-module **/

import { FormController } from "@web/views/form/form_controller";
import { formView } from "@web/views/form/form_view";
import { registry } from "@web/core/registry";
import { onMounted, useEffect } from "@odoo/owl";

export class MrpMasterPrintWizardFormController extends FormController {
    setup() {
        super.setup();

        onMounted(() => {
            // Safely get report_kind for initial setup
            const reportKind = this.model.data ? this.model.data.report_kind : undefined;
            this._toggleCurvadoSubVisibility(reportKind);
        });

        useEffect(
            (reportKind) => {
                this._toggleCurvadoSubVisibility(reportKind);
            },
            // Safely provide report_kind for dependency tracking
            () => [this.model.data && this.model.data.report_kind]
        );
    }

    _toggleCurvadoSubVisibility(reportKind) {
        if (!this.el) { // Add this check
            return;
        }
        const curvadoSubField = this.el.querySelector(".o_field_curvado_sub");
        if (curvadoSubField) {
            // Only hide if reportKind is for reports without curvado sub
            if (reportKind === 'corte_pvb' || reportKind === 'pvb_medidas_figura') {
                curvadoSubField.style.display = 'none';
            } else {
                curvadoSubField.style.display = 'block';
            }
        }
    }
}

registry.category("views").add("mrp_master_print_wizard_form", {
    ...formView,
    Controller: MrpMasterPrintWizardFormController,
});
