frappe.ui.form.on("Item", {
	refresh(frm) {
		if (frm.is_new()) return;
		if (!frappe.user_roles.includes("Catalogue Manager")) return;
		if (frm.doc.status_label !== "Active") return;

		frm.add_custom_button(__("Initiate Change"), () => {
			frappe.prompt(
				{
					fieldname: "change_type",
					fieldtype: "Select",
					label: __("Change type"),
					options: ["Update", "Suspend", "Retire"],
					reqd: 1,
				},
				(values) => {
					const prefill = { item: frm.doc.name, change_type: values.change_type };
					if (values.change_type === "Update") {
						Object.assign(prefill, {
							item_name: frm.doc.item_name,
							description: frm.doc.description,
							pack_size: frm.doc.pack_size,
							storage_requirements: frm.doc.storage_requirements,
							strength_dosage_form: frm.doc.strength_dosage_form,
							specifications: frm.doc.specifications,
							default_unit_price: frm.doc.standard_rate,
							min_order_qty: frm.doc.min_order_qty,
							max_order_qty: frm.doc.max_order_qty,
						});
					}
					frappe.new_doc("Commodity Change Request", prefill);
				},
				__("Initiate Change")
			);
		}, __("Catalogue"));
	},
});
