import frappe
from frappe import _
from frappe.utils import add_days, cint, now_datetime

CHILD_TABLE_FIELDS = {
	"commodities": {"commodity_category", "commodity", "regulatory_registration_number"},
	"documents": {"document_type", "attachment"},
}

ALLOWED_FIELDS = {
	"vendor_type",
	"legal_entity_name",
	"trading_name",
	"country_of_incorporation",
	"company_registration_number",
	"registered_address",
	"primary_contact_name",
	"primary_contact_role",
	"primary_contact_email",
	"primary_contact_phone",
	"bank_name",
	"branch_name",
	"account_name",
	"account_number",
	"swift_bic_code",
	*CHILD_TABLE_FIELDS.keys(),
}


def _sanitize_child_rows(fieldname, rows):
	allowed = CHILD_TABLE_FIELDS[fieldname]
	cleaned = []
	for row in rows or []:
		if isinstance(row, dict):
			cleaned.append({k: v for k, v in row.items() if k in allowed})
	return cleaned


def _sanitize(data):
	cleaned = {}
	for key in ALLOWED_FIELDS:
		if key not in data:
			continue
		if key in CHILD_TABLE_FIELDS:
			cleaned[key] = _sanitize_child_rows(key, data[key])
		else:
			cleaned[key] = data[key]
	return cleaned


@frappe.whitelist(allow_guest=True)
def get_vendor_registration_info():
	settings = frappe.get_single("Invitation Settings")
	return {
		"expected_processing_days": settings.expected_processing_days,
		"support_email": settings.support_email,
		"support_phone": settings.support_phone,
	}


@frappe.whitelist(allow_guest=True)
def resume_vendor_draft(token):
	name = frappe.db.get_value("Vendor Application", {"draft_token": token, "status": "Draft"}, "name")
	if not name:
		return {
			"valid": False,
			"message": _("This draft link is invalid or the application has already been submitted."),
		}

	doc = frappe.get_doc("Vendor Application", name)
	if doc.draft_expires_on and doc.draft_expires_on < now_datetime():
		return {"valid": False, "message": _("This draft has expired. Please start a new application.")}

	return {"valid": True, "application_name": doc.name, "prefill": _sanitize(doc.as_dict())}


@frappe.whitelist(allow_guest=True)
def save_vendor_application(data, draft=False, resuming_token=None):
	if isinstance(data, str):
		data = frappe.parse_json(data)

	is_draft = bool(cint(draft)) if not isinstance(draft, bool) else draft
	cleaned = _sanitize(data or {})
	cleaned["status"] = "Draft" if is_draft else "Under Review"

	existing_name = None
	if resuming_token:
		existing_name = frappe.db.get_value(
			"Vendor Application", {"draft_token": resuming_token, "status": "Draft"}, "name"
		)

	if existing_name:
		doc = frappe.get_doc("Vendor Application", existing_name)
		doc.update(cleaned)
		doc.flags.ignore_mandatory = is_draft
		doc.save(ignore_permissions=True)
	else:
		cleaned["doctype"] = "Vendor Application"
		doc = frappe.get_doc(cleaned)
		doc.insert(ignore_permissions=True, ignore_mandatory=is_draft)

	result = doc.as_dict()
	if is_draft:
		result["_draft_expiry_days"] = frappe.get_single("Invitation Settings").draft_expiry_days
	return result


@frappe.whitelist(allow_guest=True)
def resume_rfi_response(token):
	name = frappe.db.get_value("Vendor RFI", {"response_token": token, "status": "Pending Response"}, "name")
	if not name:
		return {
			"valid": False,
			"message": _("This link is invalid or this request has already been answered."),
		}

	doc = frappe.get_doc("Vendor RFI", name)
	return {
		"valid": True,
		"prefill": {
			"explanation": doc.explanation,
			"response_deadline": doc.response_deadline,
			"items": [
				{
					"description": row.description,
					"response_text": row.response_text,
					"response_attachment": row.response_attachment,
				}
				for row in doc.items
			],
		},
	}


@frappe.whitelist(allow_guest=True)
def submit_rfi_response(token, items=None):
	if isinstance(items, str):
		items = frappe.parse_json(items)

	name = frappe.db.get_value("Vendor RFI", {"response_token": token, "status": "Pending Response"}, "name")
	if not name:
		frappe.throw(_("This link is invalid or this request has already been answered."))

	doc = frappe.get_doc("Vendor RFI", name)
	for idx, row in enumerate(doc.items):
		if items and idx < len(items) and isinstance(items[idx], dict):
			row.response_text = items[idx].get("response_text")
			row.response_attachment = items[idx].get("response_attachment")

	doc.mark_responded()
	return {"vendor_application": doc.vendor_application}


def send_draft_expiry_notices():
	"""Daily scheduled job: warn vendor application drafts nearing expiry, and expire drafts past their deadline."""
	from elmn.api.notification.vendor import notify_draft_expiry_warning

	now = now_datetime()
	settings = frappe.get_single("Invitation Settings")
	warning_cutoff = add_days(now, settings.draft_expiry_warning_days or 3)

	due_for_warning = frappe.get_all(
		"Vendor Application",
		filters={
			"status": "Draft",
			"draft_expiry_warning_sent": 0,
			"draft_expires_on": ["<=", warning_cutoff],
		},
		pluck="name",
	)
	for name in due_for_warning:
		doc = frappe.get_doc("Vendor Application", name)
		if not doc.draft_expires_on or doc.draft_expires_on <= now:
			continue
		notify_draft_expiry_warning(doc)
		frappe.db.set_value(doc.doctype, doc.name, "draft_expiry_warning_sent", 1)

	expired = frappe.get_all(
		"Vendor Application",
		filters={"status": "Draft", "draft_expires_on": ["<=", now]},
		pluck="name",
	)
	for name in expired:
		frappe.db.set_value("Vendor Application", name, "status", "Expired")
