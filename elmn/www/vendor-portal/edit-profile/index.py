import frappe
from frappe import _

from elmn.api.vendor import HIGH_RISK_PROFILE_FIELDS, LOW_RISK_PROFILE_FIELDS


def get_context(context):
	if frappe.session.user == "Guest":
		frappe.throw(_("You must be logged in to view this page."), frappe.PermissionError)

	if "Supplier" not in frappe.get_roles():
		frappe.throw(_("You do not have access to the vendor portal."), frappe.PermissionError)

	supplier_name = frappe.db.get_value(
		"User Permission", {"user": frappe.session.user, "allow": "Supplier"}, "for_value"
	)
	if not supplier_name:
		frappe.throw(_("No vendor account is linked to your user."), frappe.PermissionError)

	supplier = frappe.get_doc("Supplier", supplier_name)

	context.supplier = supplier
	context.low_risk_fields = [
		{"fieldname": fieldname, "label": label, "value": supplier.get(fieldname)}
		for fieldname, label in LOW_RISK_PROFILE_FIELDS.items()
	]
	context.high_risk_fields = [
		{"fieldname": fieldname, "label": label, "value": supplier.get(fieldname)}
		for fieldname, label in HIGH_RISK_PROFILE_FIELDS.items()
	]
	context.commodity_scope = ", ".join(
		sorted(row.commodity_category for row in supplier.primary_commodity_categories)
	)

	context.requests = frappe.get_list(
		"Vendor Profile Change Request",
		filters={"supplier": supplier_name},
		fields=["name", "status", "risk_level", "creation"],
		order_by="creation desc",
		limit_page_length=20,
		ignore_permissions=True,
	)

	context.no_cache = 1
	return context
