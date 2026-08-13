frappe.ui.form.on("Contract", {
	refresh(frm) {
		if (frm.is_new() || frm.doc.party_type !== "Supplier") return;

		const can_act = ["Procurement Officer", "Purchase Manager", "System Manager"].some((role) =>
			frappe.user_roles.includes(role)
		);
		const renewable = ["Expiring Soon", "Expired"].includes(frm.doc.vendor_contract_status);

		if (can_act && renewable && frm.doc.docstatus === 1 && !frm.doc.superseded_by) {
			frm.add_custom_button(__("Renew Contract"), () => {
				frappe.confirm(
					__(
						"Create a new draft contract pre-filled from this one's terms, linked to it as the renewal?"
					),
					() => {
						frappe.call({
							method: "elmn.api.contract.renew_contract",
							args: { contract: frm.doc.name },
							freeze: true,
							callback: (r) => {
								if (r.message) {
									frappe.set_route("Form", "Contract", r.message.name);
								}
							},
						});
					}
				);
			});
		}

		if (can_act && frm.doc.docstatus === 1 && frm.doc.vendor_contract_status === "Active") {
			frm.add_custom_button(__("Amend Contract"), () => {
				frappe.prompt(
					[
						{
							fieldname: "contract_value",
							fieldtype: "Currency",
							label: __("Contract value"),
							default: frm.doc.contract_value,
						},
						{
							fieldname: "end_date",
							fieldtype: "Date",
							label: __("End date"),
							default: frm.doc.end_date,
						},
						{
							fieldname: "contract_terms",
							fieldtype: "Text Editor",
							label: __("Contract terms"),
							default: frm.doc.contract_terms,
						},
						{
							fieldname: "commodity_scope",
							fieldtype: "Small Text",
							label: __("Commodity scope (comma-separated)"),
							default: (frm.doc.commodity_scope || [])
								.map((row) => row.commodity_category)
								.sort()
								.join(", "),
						},
						{
							fieldname: "reason",
							fieldtype: "Small Text",
							label: __("Reason for amendment"),
							reqd: 1,
						},
					],
					(values) => {
						const { reason, ...changes } = values;
						frappe.call({
							method: "elmn.api.contract.initiate_amendment",
							args: { contract: frm.doc.name, changes, reason },
							freeze: true,
							callback: (r) => {
								if (r.message) {
									frappe.set_route("Form", "Contract Amendment Request", r.message.name);
								}
							},
						});
					},
					__("Amend Contract"),
					__("Submit for Approval")
				);
			});
		}

		if (can_act && frm.doc.docstatus === 1 && frm.doc.vendor_contract_status === "Active") {
			frm.add_custom_button(__("Terminate Contract"), () => {
				frappe.prompt(
					[
						{
							fieldname: "termination_reason_category",
							fieldtype: "Select",
							label: __("Reason category"),
							options:
								"Persistent Non-Performance\nRegulatory Suspension\nMutual Agreement\nBreach of Contract\nOther",
							reqd: 1,
						},
						{
							fieldname: "reason",
							fieldtype: "Small Text",
							label: __("Detailed reason"),
							reqd: 1,
						},
						{
							fieldname: "supporting_document",
							fieldtype: "Attach",
							label: __("Supporting documentation"),
							reqd: 1,
						},
					],
					(values) => {
						frappe.call({
							method: "elmn.api.contract.initiate_termination",
							args: {
								contract: frm.doc.name,
								termination_reason_category: values.termination_reason_category,
								reason: values.reason,
								supporting_document: values.supporting_document,
							},
							freeze: true,
							callback: (r) => {
								if (r.message) {
									frappe.set_route("Form", "Contract Termination Request", r.message.name);
								}
							},
						});
					},
					__("Terminate Contract"),
					__("Submit for Approval")
				);
			}, __("Terminate"));
		}

		if (frm.doc.renewed_from || frm.doc.superseded_by) {
			frm.add_custom_button(__("View History"), () => {
				frappe.call({
					method: "elmn.api.contract.get_contract_version_history",
					args: { contract: frm.doc.name },
					freeze: true,
					callback: (r) => show_contract_history(frm, r.message || []),
				});
			});
		}

		if (frm.doc.renewed_from) {
			frm.dashboard.set_headline_alert(
				__("This contract renews {0}.", [
					`<a href="/app/contract/${frm.doc.renewed_from}">${frm.doc.renewed_from}</a>`,
				])
			);
		}
	},
});

function show_contract_history(frm, versions) {
	const row_html = versions
		.map((v) => {
			const changes_html = v.changes.length
				? "<ul style='margin:0;padding-left:18px;'>" +
				  v.changes
						.map(
							(c) =>
								`<li>${frappe.utils.escape_html(c.field_label)}: ${frappe.utils.escape_html(
									c.old_value || "-"
								)} &rarr; ${frappe.utils.escape_html(c.new_value || "-")}</li>`
						)
						.join("") +
				  "</ul>"
				: "-";
			const is_current = v.contract === frm.doc.name;
			return `<tr${is_current ? " style='background:var(--bg-blue, #eaf2ff);'" : ""}>
				<td>${v.version}</td>
				<td><a href="/app/contract/${v.contract}">${v.contract}</a></td>
				<td>${frappe.utils.escape_html(v.change_type)}</td>
				<td>${frappe.utils.escape_html(v.status)}</td>
				<td>${v.activated_on ? frappe.datetime.str_to_user(v.activated_on) : "-"}</td>
				<td>${v.superseded_on ? frappe.datetime.str_to_user(v.superseded_on) : "-"}</td>
				<td>${v.activated_by || "-"}</td>
				<td>${changes_html}</td>
			</tr>`;
		})
		.join("");

	const dialog = new frappe.ui.Dialog({
		title: __("Contract Version History"),
		size: "extra-large",
		fields: [
			{
				fieldname: "history_html",
				fieldtype: "HTML",
				options: `<div style="overflow-x:auto;">
					<table class="table table-bordered">
						<thead>
							<tr>
								<th>${__("Version")}</th>
								<th>${__("Contract")}</th>
								<th>${__("Type")}</th>
								<th>${__("Status")}</th>
								<th>${__("Activated")}</th>
								<th>${__("Superseded")}</th>
								<th>${__("Approved by")}</th>
								<th>${__("Changed terms")}</th>
							</tr>
						</thead>
						<tbody>${row_html}</tbody>
					</table>
				</div>`,
			},
		],
	});
	dialog.show();
}
