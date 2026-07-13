import frappe
from frappe import _
from frappe.utils import getdate, now_datetime, sha256_hash, validate_email_address
from frappe.utils.csvutils import read_csv_content

TRACKED_BEFORE_START = ("Pending", "Sent", "Opened", "Link Clicked", "Resent")


@frappe.whitelist()
def bulk_upload(file_url):
	"""Create and send a Facility Invitation for each valid row in an uploaded CSV.

	Expected columns (header row optional): facility legal name, contact email,
	region, district, phone, physical address, facility level.
	"""
	file_doc = frappe.get_doc("File", {"file_url": file_url})
	rows = read_csv_content(file_doc.get_content())

	created = 0
	failed = []

	for idx, row in enumerate(rows, start=1):
		if not row or not any((row or [])[:2]):
			continue

		legal_name = (row[0] or "").strip() if len(row) > 0 else ""
		contact_email = (row[1] or "").strip() if len(row) > 1 else ""

		# allow a header row without forcing the caller to strip it themselves
		if idx == 1 and contact_email.lower() in ("contact email", "email"):
			continue

		if not legal_name or not contact_email or not validate_email_address(contact_email):
			failed.append({"row": idx, "reason": _("Missing or invalid facility name / contact email")})
			continue

		try:
			frappe.get_doc(
				{
					"doctype": "Facility Invitation",
					"facility_legal_name": legal_name,
					"contact_email": contact_email,
					"prefill_region": row[2].strip() if len(row) > 2 and row[2] else None,
					"prefill_district": row[3].strip() if len(row) > 3 and row[3] else None,
					"prefill_phone": row[4].strip() if len(row) > 4 and row[4] else None,
					"prefill_physical_address": row[5].strip() if len(row) > 5 and row[5] else None,
				}
			).insert()
			created += 1
		except Exception as e:
			frappe.log_error(title="Facility Invitation bulk upload row failed")
			failed.append({"row": idx, "reason": str(e)})

	return {"created": created, "failed": failed}


@frappe.whitelist(allow_guest=True)
def validate_and_start_invitation(token):
	"""Validate a public registration token and mark the invitation as opened.

	Returns {"valid": False, "message": ...} or {"valid": True, "invitation": name}.
	"""
	if not token:
		return {"valid": False, "message": _("Missing invitation token.")}

	invitation_name = frappe.db.get_value(
		"Facility Invitation", {"invitation_token_hash": sha256_hash(token)}, "name"
	)
	if not invitation_name:
		return {"valid": False, "message": _("This invitation link is invalid.")}

	invitation = frappe.get_doc("Facility Invitation", invitation_name)

	if invitation.status == "Cancelled":
		return {"valid": False, "message": _("This invitation has been cancelled.")}

	if invitation.status not in TRACKED_BEFORE_START and invitation.status != "Registration Started":
		# already submitted (Registration Submitted) — link is single-use past that point
		return {"valid": False, "message": _("This invitation has already been used.")}

	if invitation.expiry_date and getdate(invitation.expiry_date) < getdate():
		frappe.db.set_value("Facility Invitation", invitation.name, "status", "Expired")
		return {"valid": False, "message": _("This invitation link has expired.")}

	updates = {}
	if not invitation.link_clicked_on:
		updates["link_clicked_on"] = now_datetime()
	if invitation.status in TRACKED_BEFORE_START:
		updates["status"] = "Registration Started"
		updates["registration_started_on"] = invitation.registration_started_on or now_datetime()

	if updates:
		frappe.db.set_value("Facility Invitation", invitation.name, updates)

	return {
		"valid": True,
		"invitation": invitation.name,
		"prefill": {
			"facility_name": invitation.facility_legal_name,
			"contact_person_email": invitation.contact_email,
			"facility_level": invitation.prefill_facility_level,
			"region": invitation.prefill_region,
			"district": invitation.prefill_district,
			"physical_address": invitation.prefill_physical_address,
			"contact_person_phone": invitation.prefill_phone,
		},
	}
