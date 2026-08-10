frappe.ui.form.on("Supplier", {
	refresh(frm) {
		if (frm.is_new()) return;
		if (!frappe.user_roles.includes("Clinical/Pharmacy Reviewer") && !frappe.user_roles.includes("System Manager")) return;

		frm.add_custom_button(__("Edit Vendor Profile"), () => {
			frappe.prompt(
				[
					{ fieldname: "primary_contact_name", fieldtype: "Data", label: __("Primary Contact Name"), default: frm.doc.primary_contact_name },
					{ fieldname: "primary_contact_phone", fieldtype: "Data", label: __("Primary Contact Phone"), default: frm.doc.primary_contact_phone },
					{ fieldname: "primary_contact_email", fieldtype: "Data", label: __("Primary Contact Email"), default: frm.doc.primary_contact_email },
					{ fieldname: "registered_address", fieldtype: "Small Text", label: __("Registered Address"), default: frm.doc.registered_address },
					{ fieldname: "supplier_name", fieldtype: "Data", label: __("Legal Name"), default: frm.doc.supplier_name },
					{ fieldname: "column_break_edit_profile", fieldtype: "Column Break" },
					{
						fieldname: "primary_commodity_categories",
						fieldtype: "Small Text",
						label: __("Commodity Scope (comma-separated)"),
						default: (frm.doc.primary_commodity_categories || [])
							.map((row) => row.commodity_category)
							.sort()
							.join(", "),
					},
				],
				(values) => {
					frappe.call({
						method: "elmn.api.vendor.update_vendor_profile",
						args: { supplier: frm.doc.name, changes: values },
						freeze: true,
						callback: () => frm.reload_doc(),
					});
				},
				__("Edit Vendor Profile"),
				__("Save Changes")
			);
		}, __("Vendor Registry"));
	},
});
