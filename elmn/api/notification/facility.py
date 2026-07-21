import frappe
from frappe import _

from elmn.api.emails import send_templated_email
from elmn.api.notification import create_notification_log as _create_notification_log
from elmn.api.notification import users_with_role as _users_with_role


def notify_registration_officer(facility):
	users = _users_with_role("Registration Officer") or _users_with_role("System Manager")
	if not users:
		frappe.logger().warning(
			f"Facility {facility.name}: no Registration Officer (or System Manager) to notify"
		)
		return

	recipients = [u.email for u in users if u.email]
	subject = _("Facility registration pending verification: {0}").format(facility.facility_name)

	send_templated_email(
		"registration_pending",
		recipients,
		{
			"facility_name": facility.facility_name,
			"facility_registration_number": facility.facility_registration_number,
			"contact_person_name": facility.contact_person_name,
			"url": frappe.utils.get_url(f"/app/facility/{facility.name}"),
		},
		default_subject=subject,
		reference_doctype=facility.doctype,
		reference_name=facility.name,
	)
	_create_notification_log([u.name for u in users], subject, facility)


def notify_applicant_submission_received(facility):
	if not facility.contact_person_email:
		return

	expected_processing_days = frappe.db.get_single_value(
		"Invitation Settings", "expected_processing_days"
	) or 5

	send_templated_email(
		"submission_confirmation",
		[facility.contact_person_email],
		{
			"contact_person_name": facility.contact_person_name,
			"facility_name": facility.facility_name,
			"reference_number": facility.name,
			"expected_processing_days": expected_processing_days,
		},
		default_subject=_("We've received your facility registration: {0}").format(facility.name),
		reference_doctype=facility.doctype,
		reference_name=facility.name,
	)


def notify_rejection(facility):
	if not facility.contact_person_email:
		return

	send_templated_email(
		"rejection",
		[facility.contact_person_email],
		{
			"contact_person_name": facility.contact_person_name,
			"facility_name": facility.facility_name,
			"reason": facility.rejection_reason,
		},
		default_subject=_("Your facility registration was not approved"),
		reference_doctype=facility.doctype,
		reference_name=facility.name,
	)


def notify_approval(facility):
	if not facility.contact_person_email:
		return

	send_templated_email(
		"approval",
		[facility.contact_person_email],
		{
			"contact_person_name": facility.contact_person_name,
			"facility_name": facility.facility_name,
			"reference_number": facility.name,
		},
		default_subject=_("Your facility registration was approved: {0}").format(facility.facility_name),
		reference_doctype=facility.doctype,
		reference_name=facility.name,
	)


def send_activation_email(user_doc, contact_person_name, facility_name, reference_doctype=None, reference_name=None):
	"""Send a branded, Email-Template-editable activation link instead of Frappe's generic reset-password email."""
	user_doc.validate_reset_password()
	link = user_doc._reset_password(send_email=False)

	send_templated_email(
		"facility_account_activation",
		[user_doc.email],
		{
			"contact_person_name": contact_person_name,
			"facility_name": facility_name,
			"link": link,
		},
		default_subject=_("Activate your eLMN account"),
		reference_doctype=reference_doctype,
		reference_name=reference_name,
	)


def provision_facility_account(facility):
	"""Create (or reuse) the facility's first user account and email an activation link."""
	if facility.facility_user:
		return

	if not facility.contact_person_email:
		frappe.throw(_("Contact person email is required to activate a facility account."))

	user_name = frappe.db.get_value("User", facility.contact_person_email, "name")
	if not user_name:
		user = frappe.get_doc(
			{
				"doctype": "User",
				"email": facility.contact_person_email,
				"first_name": facility.contact_person_name or facility.facility_name,
				"user_type": "System User",
				"send_welcome_email": 0,
			}
		)
		user.append("roles", {"role": "Facility Manager"})
		user.insert(ignore_permissions=True)
		user_name = user.name

	frappe.db.set_value("User", user_name, "custom_facility", facility.name)
	facility.db_set("facility_user", user_name)
	facility.db_set("account_status", "Pending Activation")

	user_doc = frappe.get_doc("User", user_name)
	send_activation_email(
		user_doc, facility.contact_person_name, facility.facility_name, facility.doctype, facility.name
	)


def on_facility_update(doc, method=None):
	"""Dispatch a notification off the Facility's workflow state on each transition."""
	if not doc.has_value_changed("workflow_state"):
		return

	if doc.workflow_state == "Registration Officer":
		notify_registration_officer(doc)
		notify_applicant_submission_received(doc)
	elif doc.workflow_state == "Rejected":
		notify_rejection(doc)
	elif doc.workflow_state == "Approved":
		provision_facility_account(doc)
		notify_approval(doc)


def on_user_update(doc, method=None):
	"""Mark the owning facility's account Active once its user completes the activation link."""
	before = doc.get_doc_before_save()
	if not before or not before.reset_password_key or doc.reset_password_key:
		return

	facility_name = frappe.db.get_value("Facility", {"facility_user": doc.name}, "name")
	if not facility_name:
		return

	frappe.db.set_value(
		"Facility",
		facility_name,
		{"account_status": "Active", "facility_status": 1},
	)


def validate_employee_facility_user(doc, method=None):
	"""Enforce that Facility + Facility Role are set whenever an Employee is marked a Facility User."""
	if not doc.custom_is_facility_user:
		return
	if not doc.custom_facility:
		frappe.throw(_("Facility is required when 'Is Facility User' is checked."))
	if not doc.custom_facility_role:
		frappe.throw(_("Facility Role is required when 'Is Facility User' is checked."))


def on_employee_update(doc, method=None):
	"""Provision (or revoke) the linked User's Facility access when the Facility User flag/fields change."""
	if not doc.has_value_changed("custom_is_facility_user") and not doc.has_value_changed(
		"custom_facility"
	) and not doc.has_value_changed("custom_facility_role"):
		return

	if doc.custom_is_facility_user:
		_provision_facility_employee_user(doc)
	elif doc.has_value_changed("custom_is_facility_user"):
		_revoke_facility_employee_user(doc)


def _provision_facility_employee_user(doc):
	linked_user = doc.user_id and frappe.db.get_value("User", doc.user_id, "custom_facility")
	if linked_user == doc.custom_facility:
		return

	email = doc.user_id or doc.company_email or doc.personal_email or doc.prefered_email
	if not email:
		frappe.throw(
			_("{0} needs a Company Email, Personal Email, or linked User before being marked as a Facility User.").format(
				doc.employee_name or doc.name
			)
		)

	user_name = doc.user_id or frappe.db.get_value("User", email, "name")
	is_new_user = not user_name

	if is_new_user:
		user = frappe.get_doc(
			{
				"doctype": "User",
				"email": email,
				"first_name": doc.employee_name or email,
				"user_type": "System User",
				"send_welcome_email": 0,
			}
		)
		user.append("roles", {"role": doc.custom_facility_role})
		user.insert(ignore_permissions=True)
		user_name = user.name
	else:
		user = frappe.get_doc("User", user_name)
		if not any(r.role == doc.custom_facility_role for r in user.roles):
			user.append("roles", {"role": doc.custom_facility_role})
			user.save(ignore_permissions=True)

	if doc.user_id != user_name:
		frappe.db.set_value("Employee", doc.name, "user_id", user_name)

	frappe.db.set_value("User", user_name, "custom_facility", doc.custom_facility)

	if is_new_user:
		user_doc = frappe.get_doc("User", user_name)
		facility_name = frappe.db.get_value("Facility", doc.custom_facility, "facility_name") or doc.custom_facility
		send_activation_email(user_doc, doc.employee_name, facility_name, doc.doctype, doc.name)


def _revoke_facility_employee_user(doc):
	if not doc.user_id:
		return
	if not frappe.db.get_value("User", doc.user_id, "custom_facility"):
		return
	frappe.db.set_value("User", doc.user_id, "custom_facility", None)
