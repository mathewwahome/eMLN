frappe.listview_settings["Facility Invitation"] = {
	add_fields: ["status", "contact_email", "sent_on", "expiry_date"],
	get_indicator(doc) {
		const colors = {
			Pending: "grey",
			Sent: "blue",
			Opened: "blue",
			"Link Clicked": "orange",
			"Registration Started": "orange",
			"Registration Submitted": "green",
			Resent: "blue",
			Expired: "red",
			Cancelled: "red",
		};
		return [__(doc.status), colors[doc.status] || "grey", "status,=," + doc.status];
	},
	onload(listview) {
		listview.page.add_inner_button(__("Bulk Upload"), () => {
			const dialog = new frappe.ui.Dialog({
				title: __("Bulk Invitation Upload"),
				fields: [
					{
						fieldname: "csv_file",
						fieldtype: "Attach",
						label: __("CSV File"),
						reqd: 1,
						description: __(
							"Columns: facility legal name, contact email, and optionally region, district, phone, physical address, facility level."
						),
					},
				],
				primary_action_label: __("Upload & Send"),
				primary_action(values) {
					frappe.call({
						method: "elmn.api.invitation.bulk_upload",
						args: { file_url: values.csv_file },
						freeze: true,
						freeze_message: __("Sending invitations..."),
						callback(r) {
							dialog.hide();
							const result = r.message || {};
							const failed = result.failed || [];
							let message = __("{0} invitation(s) created and sent.", [result.created || 0]);
							if (failed.length) {
								message +=
									"<br><br>" +
									__("{0} row(s) failed:", [failed.length]) +
									"<ul>" +
									failed
										.map((f) => `<li>${__("Row")} ${f.row}: ${frappe.utils.escape_html(f.reason)}</li>`)
										.join("") +
									"</ul>";
							}
							frappe.msgprint({
								title: __("Bulk Upload Complete"),
								message,
								indicator: failed.length ? "orange" : "green",
							});
							listview.refresh();
						},
					});
				},
			});
			dialog.show();
		});
	},
};
