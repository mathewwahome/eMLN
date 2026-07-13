// Copyright (c) 2026, IntelliSOFT Consulting and contributors
// For license information, please see license.txt

frappe.ui.form.on("Facility", {
	before_workflow_action(frm) {
		if (frm.selected_workflow_action !== "Reject") return;

		return new Promise((resolve) => {
			frappe.prompt(
				{
					fieldname: "rejection_reason",
					fieldtype: "Small Text",
					label: __("Rejection reason"),
					reqd: 1,
				},
				(values) => {
					frm.set_value("rejection_reason", values.rejection_reason);
					frm.save().then(resolve);
				},
				__("Reject Facility Registration")
			);
		});
	},
});
