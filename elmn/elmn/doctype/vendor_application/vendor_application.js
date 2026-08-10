// Copyright (c) 2026, IntelliSOFT Consulting and contributors
// For license information, please see license.txt

frappe.ui.form.on("Vendor Application", {
	refresh(frm) {
		if (frm.is_new()) return;

		if (frm.doc.document_review_status !== "Completed") {
			frm.add_custom_button(__("Complete Document Review"), () => {
				frappe.confirm(
					__("Every document must have a review status before the review can be completed. Continue?"),
					() => {
						frm.call("complete_document_review").then(() => frm.reload_doc());
					}
				);
			}, __("Document Review"));
		}

		if (frm.doc.verification_status !== "Completed") {
			frm.add_custom_button(__("Complete Verification"), () => {
				frappe.confirm(
					__("Every mandatory check must have a recorded outcome before verification can be completed. Continue?"),
					() => {
						frm.call("complete_verification").then(() => frm.reload_doc());
					}
				);
			}, __("Verification"));
		}

		if (frm.doc.status === "Approved" && (!frm.doc.supplier || !frm.doc.vendor_user)) {
			frm.add_custom_button(__("Link Supplier Account"), () => {
				frm.call("link_supplier_account").then(() => frm.reload_doc());
			}, __("Approval"));
		}

		if (frm.doc.status === "Approved" && frm.doc.supplier) {
			frm.add_custom_button(__("Change Vendor User"), () => {
				frappe.prompt(
					{
						fieldname: "new_user",
						fieldtype: "Link",
						options: "User",
						label: __("New vendor user"),
						reqd: 1,
						get_query: () => ({ filters: { user_type: "System User", enabled: 1 } }),
					},
					(values) => {
						frm.call("change_vendor_user", { new_user: values.new_user }).then(() => frm.reload_doc());
					},
					__("Change Vendor User")
				);
			}, __("Approval"));
		}

		if (frm.doc.status === "Under Review") {
			frm.add_custom_button(__("Request Additional Information"), () => {
				frappe.new_doc("Vendor RFI", { vendor_application: frm.doc.name });
			});

			frm.add_custom_button(__("Approve"), () => {
				frappe.prompt(
					{
						fieldname: "notes",
						fieldtype: "Small Text",
						label: __("Approval notes"),
					},
					(values) => {
						frm.call("approve", { notes: values.notes }).then(() => frm.reload_doc());
					},
					__("Approve Vendor Application")
				);
			}, __("Approval"));
		}
	},
});
