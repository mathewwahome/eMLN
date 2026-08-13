// Copyright (c) 2026, IntelliSOFT Consulting and contributors
// For license information, please see license.txt

frappe.ui.form.on("Contract Termination Request", {
	refresh(frm) {
		if (frm.is_new()) return;

		const roles = frappe.user_roles;
		const can_approve =
			roles.includes(frm.doc.required_approval_role) || roles.includes("System Manager");

		if (frm.doc.status === "Pending Approval" && can_approve) {
			frm.add_custom_button(__("Approve"), () => {
				frappe.confirm(
					__("Terminate this contract? This cannot be undone."),
					() => {
						frappe.prompt(
							{ fieldname: "comment", fieldtype: "Small Text", label: __("Comment (optional)") },
							(values) => {
								frm.call("approve", { comment: values.comment }).then(() => frm.reload_doc());
							},
							__("Approve Termination")
						);
					}
				);
			}, __("Approval"));

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
					__("Reject Termination")
				);
			}, __("Approval"));
		}

		if (frm.doc.status === "Approved") {
			frm.dashboard.set_headline_alert(
				__("Contract terminated. {0} purchase order(s) flagged for review.", [
					frm.doc.flagged_purchase_order_count || 0,
				])
			);
		}
	},
});
