// Copyright (c) 2026, IntelliSOFT Consulting and contributors
// For license information, please see license.txt

frappe.ui.form.on("Supplier Bank Detail Change Request", {
	refresh(frm) {
		if (frm.is_new()) return;

		const roles = frappe.user_roles;
		const is_approver =
			roles.includes("Finance Officer") ||
			roles.includes("Head of Finance/Finance Approver") ||
			roles.includes("System Manager");
		const is_requester = frappe.session.user === frm.doc.requested_by;

		if (frm.doc.status === "Pending Second Approval" && is_approver && !is_requester) {
			frm.add_custom_button(__("Approve"), () => {
				frappe.confirm(
					__("Activate these banking details on the Supplier record? This cannot be undone."),
					() => {
						frappe.prompt(
							{ fieldname: "comment", fieldtype: "Small Text", label: __("Comment (optional)") },
							(values) => {
								frm.call("approve", { comment: values.comment }).then(() => frm.reload_doc());
							},
							__("Second Approval")
						);
					}
				);
			}, __("Second Approval"));

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
			}, __("Second Approval"));
		}

		if (frm.doc.status === "Pending Second Approval" && is_requester) {
			frm.dashboard.set_headline_alert(
				__("Waiting for a second Finance Officer to approve - you cannot approve your own request.")
			);
		}
	},
});
