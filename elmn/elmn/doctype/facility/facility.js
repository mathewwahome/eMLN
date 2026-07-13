// Copyright (c) 2026, IntelliSOFT Consulting and contributors
// For license information, please see license.txt

frappe.ui.form.on("Facility", {
	refresh(frm) {
		if (frm.is_new()) return;

		if (frm.doc.operational_status === "Active" && frm.doc.workflow_state === "Approved") {
			frm.add_custom_button(__("Suspend"), () => {
				frappe.prompt(
					{
						fieldname: "reason",
						fieldtype: "Small Text",
						label: __("Reason"),
						reqd: 1,
					},
					(values) => {
						frm.call("suspend", { reason: values.reason }).then(() => frm.reload_doc());
					},
					__("Suspend Facility")
				);
			}, __("Offboarding"));

			frm.add_custom_button(__("Remove"), () => {
				frappe.prompt(
					{
						fieldname: "reason",
						fieldtype: "Small Text",
						label: __("Reason"),
						reqd: 1,
					},
					(values) => {
						frm.call("remove", { reason: values.reason }).then(() => frm.reload_doc());
					},
					__("Remove Facility")
				);
			}, __("Offboarding"));
		}

		if (["Suspended", "Removed"].includes(frm.doc.operational_status)) {
			frm.add_custom_button(__("Reactivate"), () => {
				frappe.confirm(__("Reactivate this facility and restore access for its users?"), () => {
					frm.call("reactivate").then(() => frm.reload_doc());
				});
			}, __("Offboarding"));
		}
	},

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
