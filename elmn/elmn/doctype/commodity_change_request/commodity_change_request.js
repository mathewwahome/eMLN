// Copyright (c) 2026, IntelliSOFT Consulting and contributors
// For license information, please see license.txt

frappe.ui.form.on("Commodity Change Request", {
	refresh(frm) {
		if (frm.is_new()) return;

		const roles = frappe.user_roles;
		const is_clinical = roles.includes("Clinical/Pharmacy Reviewer") || roles.includes("System Manager");
		const is_procurement = roles.includes("Procurement Officer") || roles.includes("System Manager");
		const is_catalogue_manager = roles.includes("Catalogue Manager") || roles.includes("System Manager");

		if (frm.doc.status === "Draft" && is_catalogue_manager) {
			frm.add_custom_button(__("Submit for Approval"), () => {
				frappe.confirm(__("Submit this change for approval?"), () => {
					frm.call("submit_for_approval").then(() => frm.reload_doc());
				});
			});
		}

		if (frm.doc.status === "Pending Clinical Review" && is_clinical) {
			frm.add_custom_button(__("Approve"), () => {
				frappe.prompt(
					{ fieldname: "comment", fieldtype: "Small Text", label: __("Comment (optional)") },
					(values) => {
						frm.call("clinical_approve", { comment: values.comment }).then(() => frm.reload_doc());
					},
					__("Clinical Approval")
				);
			}, __("Clinical Review"));

			frm.add_custom_button(__("Reject"), () => {
				frappe.prompt(
					{
						fieldname: "reason",
						fieldtype: "Small Text",
						label: __("Rejection reason"),
						reqd: 1,
					},
					(values) => {
						frm.call("clinical_reject", { reason: values.reason }).then(() => frm.reload_doc());
					},
					__("Clinical Rejection")
				);
			}, __("Clinical Review"));
		}

		if (frm.doc.status === "Pending Procurement Review" && is_procurement) {
			frm.add_custom_button(__("Approve"), () => {
				frappe.prompt(
					{ fieldname: "comment", fieldtype: "Small Text", label: __("Comment (optional)") },
					(values) => {
						frm.call("procurement_approve", { comment: values.comment }).then(() => frm.reload_doc());
					},
					__("Procurement Approval")
				);
			}, __("Procurement Review"));

			frm.add_custom_button(__("Reject"), () => {
				frappe.prompt(
					{
						fieldname: "reason",
						fieldtype: "Small Text",
						label: __("Rejection reason"),
						reqd: 1,
					},
					(values) => {
						frm.call("procurement_reject", { reason: values.reason }).then(() => frm.reload_doc());
					},
					__("Procurement Rejection")
				);
			}, __("Procurement Review"));
		}

		if (frm.doc.status === "Rejected" && is_catalogue_manager) {
			frm.add_custom_button(__("Resubmit"), () => {
				frappe.confirm(__("Move this request back to Draft so you can edit and resubmit it?"), () => {
					frm.call("resubmit").then(() => frm.reload_doc());
				});
			});

			frm.add_custom_button(__("Close as Unresolved"), () => {
				frappe.prompt(
					{ fieldname: "note", fieldtype: "Small Text", label: __("Closure note (optional)") },
					(values) => {
						frm.call("close_unresolved", { note: values.note }).then(() => frm.reload_doc());
					},
					__("Close Request")
				);
			});
		}
	},
});
