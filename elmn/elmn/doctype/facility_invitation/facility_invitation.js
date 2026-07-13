// Copyright (c) 2026, IntelliSOFT Consulting and contributors
// For license information, please see license.txt

frappe.ui.form.on("Facility Invitation", {
	refresh(frm) {
		if (frm.is_new()) return;

		const not_resendable = ["Registration Started", "Registration Submitted", "Cancelled"];
		const not_cancellable = ["Registration Submitted", "Cancelled"];

		if (!not_resendable.includes(frm.doc.status)) {
			frm.add_custom_button(__("Resend"), () => {
				frappe.confirm(__("Send a new invitation link to {0}?", [frm.doc.contact_email]), () => {
					frm.call("resend_invitation").then(() => frm.reload_doc());
				});
			});
		}

		if (!not_cancellable.includes(frm.doc.status)) {
			frm.add_custom_button(__("Cancel Invitation"), () => {
				frappe.prompt(
					{
						fieldname: "reason",
						fieldtype: "Small Text",
						label: __("Cancellation reason"),
						reqd: 1,
					},
					(values) => {
						frm.call("cancel_invitation", { reason: values.reason }).then(() => frm.reload_doc());
					},
					__("Cancel Invitation")
				);
			});
		}
	},
});
