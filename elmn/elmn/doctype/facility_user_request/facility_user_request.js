// Copyright (c) 2026, IntelliSOFT Consulting and contributors
// For license information, please see license.txt

frappe.ui.form.on("Facility User Request", {
	refresh(frm) {
		if (frm.is_new() || frm.doc.status !== "Pending") return;

		frm.add_custom_button(__("Approve"), () => {
			frappe.confirm(
				__("Approve this request and create the user account for {0}?", [frm.doc.contact_email]),
				() => {
					frm.call("approve").then(() => frm.reload_doc());
				}
			);
		});

		frm.add_custom_button(__("Reject"), () => {
			frappe.prompt(
				{
					fieldname: "reason",
					fieldtype: "Small Text",
					label: __("Rejection reason"),
					reqd: 1,
				},
				(values) => {
					frm.call("reject", { reason: values.reason }).then(() => frm.reload_doc());
				},
				__("Reject Request")
			);
		});
	},
});
