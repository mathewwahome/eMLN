import frappe
from frappe import _
from frappe.utils import add_days, cint, now_datetime, today

CHILD_TABLE_FIELDS = {
	"commodities": {"commodity_category", "commodity", "regulatory_registration_number"},
	"documents": {"document_type", "attachment"},
}

LOW_RISK_PROFILE_FIELDS = {
	"primary_contact_name": "Primary contact name",
	"primary_contact_phone": "Primary contact phone",
	"primary_contact_email": "Primary contact email",
	"registered_address": "Registered address",
}

HIGH_RISK_PROFILE_FIELDS = {
	"bank_name": "Bank name",
	"branch_name": "Branch name",
	"account_name": "Account name",
	"account_number": "Account number",
	"swift_bic_code": "SWIFT/BIC code",
	"supplier_name": "Legal name",
}

COMMODITY_SCOPE_FIELD = "primary_commodity_categories"
COMMODITY_SCOPE_LABEL = "Commodity scope"

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


def update_supplier_last_activity(doc, method=None):
	if doc.supplier:
		frappe.db.set_value("Supplier", doc.supplier, "last_activity_date", today())


REVIEWER_ROLES = {"Clinical/Pharmacy Reviewer", "System Manager"}


def _require_reviewer_role():
	if not set(frappe.get_roles()) & REVIEWER_ROLES:
		frappe.throw(_("You do not have access to edit vendor profiles."), frappe.PermissionError)


def _diff_profile_changes(supplier, changes):
	rows = []
	for fieldname, value in (changes or {}).items():
		is_high_risk = False

		if fieldname in LOW_RISK_PROFILE_FIELDS:
			label = LOW_RISK_PROFILE_FIELDS[fieldname]
			old_value = supplier.get(fieldname)
		elif fieldname in HIGH_RISK_PROFILE_FIELDS:
			label = HIGH_RISK_PROFILE_FIELDS[fieldname]
			old_value = supplier.get(fieldname)
			is_high_risk = True
		elif fieldname == COMMODITY_SCOPE_FIELD:
			label = COMMODITY_SCOPE_LABEL
			old_value = ", ".join(
				sorted(row.commodity_category for row in supplier.primary_commodity_categories)
			)
			value = ", ".join(sorted({v.strip() for v in (value or "").split(",") if v.strip()}))
			is_high_risk = True
		else:
			frappe.throw(_("{0} is not an editable vendor profile field.").format(fieldname))

		if (old_value or "") == (value or ""):
			continue

		rows.append(
			{
				"fieldname": fieldname,
				"field_label": label,
				"old_value": old_value,
				"new_value": value,
				"is_high_risk": is_high_risk,
			}
		)

	if not rows:
		frappe.throw(_("No changes were submitted."))

	return rows


@frappe.whitelist()
def update_vendor_profile(supplier, changes):
	if isinstance(changes, str):
		changes = frappe.parse_json(changes)

	_require_reviewer_role()

	doc = frappe.get_doc("Supplier", supplier)
	rows = _diff_profile_changes(doc, changes)

	for row in rows:
		if row["fieldname"] == COMMODITY_SCOPE_FIELD:
			categories = [c.strip() for c in (row["new_value"] or "").split(",") if c.strip()]
			doc.set("primary_commodity_categories", [{"commodity_category": c} for c in categories])
		else:
			doc.set(row["fieldname"], row["new_value"])
	doc.save(ignore_permissions=True)

	log = frappe.get_doc(
		{
			"doctype": "Vendor Profile Change Request",
			"supplier": supplier,
			"requested_by": frappe.session.user,
			"status": "Approved",
			"risk_level": "High" if any(row["is_high_risk"] for row in rows) else "Low",
			"changes": rows,
			"reviewed_by": frappe.session.user,
			"reviewed_on": now_datetime(),
		}
	)
	log.insert(ignore_permissions=True)

	return {"name": log.name}


def _caller_supplier():
	if frappe.session.user == "Guest":
		frappe.throw(_("You must be logged in."), frappe.PermissionError)
	if "Supplier" not in frappe.get_roles():
		frappe.throw(_("You do not have access to submit vendor profile changes."), frappe.PermissionError)

	supplier = frappe.db.get_value(
		"User Permission", {"user": frappe.session.user, "allow": "Supplier"}, "for_value"
	)
	if not supplier:
		frappe.throw(_("No vendor account is linked to your user."), frappe.PermissionError)
	return supplier


@frappe.whitelist()
def submit_profile_change_request(changes):
	if isinstance(changes, str):
		changes = frappe.parse_json(changes)

	supplier_name = _caller_supplier()
	supplier = frappe.get_doc("Supplier", supplier_name)

	rows = []
	for fieldname, value in (changes or {}).items():
		is_high_risk = False

		if fieldname in LOW_RISK_PROFILE_FIELDS:
			label = LOW_RISK_PROFILE_FIELDS[fieldname]
			old_value = supplier.get(fieldname)
		elif fieldname in HIGH_RISK_PROFILE_FIELDS:
			label = HIGH_RISK_PROFILE_FIELDS[fieldname]
			old_value = supplier.get(fieldname)
			is_high_risk = True
		elif fieldname == COMMODITY_SCOPE_FIELD:
			label = COMMODITY_SCOPE_LABEL
			old_value = ", ".join(
				sorted(row.commodity_category for row in supplier.primary_commodity_categories)
			)
			value = ", ".join(sorted({v.strip() for v in (value or "").split(",") if v.strip()}))
			is_high_risk = True
		else:
			frappe.throw(_("{0} is not an editable vendor profile field.").format(fieldname))

		if (old_value or "") == (value or ""):
			continue

		rows.append(
			{
				"fieldname": fieldname,
				"field_label": label,
				"old_value": old_value,
				"new_value": value,
				"is_high_risk": is_high_risk,
			}
		)

	if not rows:
		frappe.throw(_("No changes were submitted."))

	request = frappe.get_doc(
		{
			"doctype": "Vendor Profile Change Request",
			"supplier": supplier_name,
			"requested_by": frappe.session.user,
			"status": "Pending MLN Review",
			"risk_level": "High" if any(row["is_high_risk"] for row in rows) else "Low",
			"changes": rows,
		}
	)
	request.insert(ignore_permissions=True)

	_notify_reviewers_of_change_request(request)

	return {"name": request.name}


def _notify_reviewers_of_change_request(request):
	from elmn.api.emails import send_templated_email
	from elmn.api.notification import create_notification_log, users_with_role

	users = users_with_role("Clinical/Pharmacy Reviewer") or users_with_role("System Manager")
	if not users:
		return

	supplier_name = frappe.db.get_value("Supplier", request.supplier, "supplier_name")
	subject = _("Vendor profile change request submitted: {0}").format(supplier_name)

	send_templated_email(
		"vendor_profile_change_request_submitted",
		[u.email for u in users if u.email],
		{
			"requested_by": request.requested_by,
			"supplier_name": supplier_name,
			"risk_level": request.risk_level,
			"changes": [
				{"label": row.field_label, "old": row.old_value, "new": row.new_value}
				for row in request.changes
			],
			"review_url": frappe.utils.get_url(f"/app/vendor-profile-change-request/{request.name}"),
		},
		default_subject=subject,
		reference_doctype=request.doctype,
		reference_name=request.name,
	)
	create_notification_log([u.name for u in users], subject, request)


PROFILE_CHANGE_NOTIFY_FIELDS = {
	"vendor_status": "Vendor status",
	"contract_status": "Contract status",
}


def notify_vendor_of_profile_change(doc, method=None):
	if not doc.vendor_application:
		return

	before = doc.get_doc_before_save()
	if not before:
		return

	changes = [
		{"label": _(label), "old": before.get(fieldname), "new": doc.get(fieldname)}
		for fieldname, label in PROFILE_CHANGE_NOTIFY_FIELDS.items()
		if before.get(fieldname) != doc.get(fieldname)
	]

	old_categories = {row.commodity_category for row in before.primary_commodity_categories}
	new_categories = {row.commodity_category for row in doc.primary_commodity_categories}
	if old_categories != new_categories:
		changes.append(
			{
				"label": _("Commodity scope"),
				"old": ", ".join(sorted(old_categories)),
				"new": ", ".join(sorted(new_categories)),
			}
		)

	if not changes:
		return

	vendor_user, email = frappe.db.get_value(
		"Vendor Application", doc.vendor_application, ["vendor_user", "primary_contact_email"]
	) or (None, None)
	if not email:
		return

	from elmn.api.emails import send_templated_email
	from elmn.api.notification import create_notification_log

	subject = _("Your MLN vendor profile has been updated: {0}").format(doc.supplier_name)

	send_templated_email(
		"vendor_profile_changed",
		[email],
		{"supplier_name": doc.supplier_name, "changes": changes},
		default_subject=subject,
		reference_doctype=doc.doctype,
		reference_name=doc.name,
	)
	if vendor_user:
		create_notification_log([vendor_user], subject, doc)


def get_supplier_dashboard_data(data):
	data.non_standard_fieldnames = {
		**data.get("non_standard_fieldnames", {}),
		"Contract": "party_name",
	}
	data.transactions.append({"label": _("Performance"), "items": ["Supplier Scorecard"]})
	data.transactions.append({"label": _("Contracts"), "items": ["Contract"]})
	return data
