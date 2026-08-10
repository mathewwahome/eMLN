import frappe
from frappe import _


def get_context(context):
	if frappe.session.user == "Guest":
		frappe.throw(_("You must be logged in to view this page."), frappe.PermissionError)

	if "Supplier" not in frappe.get_roles():
		frappe.throw(_("You do not have access to the vendor portal."), frappe.PermissionError)

	supplier = frappe.db.get_value(
		"User Permission", {"user": frappe.session.user, "allow": "Supplier"}, "for_value"
	)
	if not supplier:
		frappe.throw(_("No vendor account is linked to your user."), frappe.PermissionError)

	context.supplier = frappe.get_doc("Supplier", supplier)

	context.purchase_orders = frappe.get_list(
		"Purchase Order",
		filters={"supplier": supplier},
		fields=["name", "transaction_date", "status", "grand_total"],
		order_by="transaction_date desc",
		limit_page_length=20,
	)

	context.purchase_receipts = frappe.get_list(
		"Purchase Receipt",
		filters={"supplier": supplier},
		fields=["name", "posting_date", "status"],
		order_by="posting_date desc",
		limit_page_length=20,
	)

	context.scorecard = (
		frappe.get_doc("Supplier Scorecard", supplier)
		if frappe.db.exists("Supplier Scorecard", supplier)
		else None
	)

	vendor_applications = frappe.get_list(
		"Vendor Application",
		filters={"vendor_user": frappe.session.user},
		pluck="name",
		ignore_permissions=True,
	)
	context.rfis = (
		frappe.get_list(
			"Vendor RFI",
			filters={"vendor_application": ["in", vendor_applications]},
			fields=["name", "status", "response_deadline"],
			order_by="response_deadline asc",
			ignore_permissions=True,
		)
		if vendor_applications
		else []
	)

	context.no_cache = 1
	return context
