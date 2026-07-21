import frappe
from frappe import _
from frappe.utils import getdate, today

from elmn.api.emails import send_templated_email
from elmn.api.notification.facility import _create_notification_log, _users_with_role

ACTIVE_PURCHASE_ORDER_STATUSES = ("On Hold", "To Receive and Bill", "To Bill", "To Receive")
EDIT_FLAG_FIELDS = ("item_name", "pack_size", "storage_requirements", "description")
INTERNAL_TRANSITION_ROLES = ("System Manager",)


@frappe.whitelist()
def suspend_item(item_code, reason):
	_require_internal_caller()
	_transition_item(item_code, "Suspended", reason)


@frappe.whitelist()
def retire_item(item_code, reason):
	_require_internal_caller()
	_transition_item(item_code, "Retired", reason)


@frappe.whitelist()
def reactivate_item(item_code):
	_require_internal_caller()
	_transition_item(item_code, "Active", None)


def _require_internal_caller():
	if not set(frappe.get_roles(frappe.session.user)) & set(INTERNAL_TRANSITION_ROLES):
		frappe.throw(_("Not permitted"), frappe.PermissionError)


def _transition_item(item_code, new_status, reason):
	if new_status != "Active" and not reason:
		frappe.throw(_("A reason is required."))

	doc = frappe.get_doc("Item", item_code)
	doc.disabled = 0 if new_status == "Active" else 1
	doc.status_label = new_status
	doc.deactivation_reason = reason
	doc.save()


def on_item_update(doc, method=None):
	"""Cascade an Item's lifecycle changes: flag active Purchase Orders, log, notify."""
	if doc.has_value_changed("status_label"):
		if doc.status_label == "Active":
			doc.add_comment("Info", _("Reactivated"))
		else:
			reason = _("Commodity {0} ({1}) was {2}: {3}").format(
				doc.item_name or doc.name, doc.name, doc.status_label.lower(), doc.deactivation_reason
			)
			if doc.status_label == "Retired" and doc.get("replacement_item"):
				reason += _(" Suggested replacement: {0}.").format(doc.replacement_item)
			_flag_active_purchase_orders(
				doc, reason, include_drafts=(doc.status_label == "Retired")
			)
			doc.add_comment("Info", _("{0}: {1}").format(doc.status_label, doc.deactivation_reason))
		return

	if doc.status_label != "Active":
		return

	changed_fields = [f for f in EDIT_FLAG_FIELDS if doc.has_value_changed(f)]
	if changed_fields:
		reason = _("Commodity {0} ({1}) was updated ({2}) - please review any in-progress order.").format(
			doc.item_name or doc.name, doc.name, ", ".join(changed_fields)
		)
		_flag_active_purchase_orders(doc, reason)


def _flag_active_purchase_orders(item, reason, include_drafts=False):
	item_filter = ["Purchase Order Item", "item_code", "=", item.name]

	po_names = frappe.get_all(
		"Purchase Order",
		filters=[
			item_filter,
			["Purchase Order", "docstatus", "=", 1],
			["Purchase Order", "status", "in", ACTIVE_PURCHASE_ORDER_STATUSES],
		],
		pluck="name",
	)

	if include_drafts:
	
		draft_names = frappe.get_all(
			"Purchase Order",
			filters=[item_filter, ["Purchase Order", "docstatus", "=", 0]],
			pluck="name",
		)
		po_names = list(dict.fromkeys(po_names + draft_names))

	if not po_names:
		return

	for po_name in po_names:
		frappe.db.set_value("Purchase Order", po_name, {"flagged_for_review": 1, "flag_reason": reason})
		frappe.get_doc("Purchase Order", po_name).add_comment("Info", reason)

	users = _users_with_role("Procurement Officer") or _users_with_role(
		"Head of Procurement/ Procurement Approver"
	)
	if not users:
		frappe.logger().warning(
			f"Item {item.name}: no Procurement Officer to notify about "
			f"{len(po_names)} flagged purchase order(s)"
		)
		return

	recipients = [u.email for u in users if u.email]
	subject = _("{0} active purchase order(s) flagged for review: commodity {1}").format(
		len(po_names), item.item_name or item.name
	)

	send_templated_email(
		"purchase_order_flagged",
		recipients,
		{
			"item_name": item.item_name or item.name,
			"item_code": item.name,
			"reason": reason,
			"purchase_orders": [
				{"name": po_name, "url": frappe.utils.get_url(f"/app/purchase-order/{po_name}")}
				for po_name in po_names
			],
		},
		default_subject=subject,
	)
	_create_notification_log([u.name for u in users], subject, item)


def apply_due_retirements():
	"""Daily scheduled job: apply any future-dated Retire that's now due."""
	due = frappe.get_all(
		"Item",
		filters={
			"scheduled_retirement_date": ["<=", today()],
			"status_label": ["!=", "Retired"],
		},
		fields=["name", "scheduled_retirement_ccr"],
	)
	for row in due:
		ccr_name = row.scheduled_retirement_ccr
		reason = _("Scheduled retirement effective {0}").format(today())
		if ccr_name and frappe.db.exists("Commodity Change Request", ccr_name):
			ccr = frappe.get_doc("Commodity Change Request", ccr_name)
			reason = ccr.retirement_reason or reason
			ccr._retire_now()
		else:
			retire_item(row.name, reason)
		frappe.db.set_value(
			"Item", row.name, {"scheduled_retirement_date": None, "scheduled_retirement_ccr": None}
		)


@frappe.whitelist()
def search_commodities(txt=""):
	"""Typeahead commodity search (CCM.023) - active commodities only."""
	filters = {"disabled": 0}
	or_filters = None
	if txt:
		or_filters = [
			["item_name", "like", f"%{txt}%"],
			["item_code", "like", f"%{txt}%"],
			["therapeutic_class", "like", f"%{txt}%"],
		]

	return frappe.get_all(
		"Item",
		filters=filters,
		or_filters=or_filters,
		fields=["name as item_code", "item_name", "stock_uom", "pack_size"],
		limit_page_length=20,
		order_by="item_name asc",
	)
