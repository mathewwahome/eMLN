import frappe
from frappe import _


def _users_with_role(role):
	user_names = frappe.get_all(
		"Has Role", filters={"role": role, "parenttype": "User"}, pluck="parent"
	)
	return frappe.get_all(
		"User", filters={"name": ["in", user_names], "enabled": 1}, pluck="email"
	)


def notify_registration_officer(facility):
	recipients = _users_with_role("Registration Officer") or _users_with_role("System Manager")
	if not recipients:
		frappe.logger().warning(
			f"Facility {facility.name}: no Registration Officer (or System Manager) to notify"
		)
		return

	frappe.sendmail(
		recipients=recipients,
		subject=_("Facility registration pending verification: {0}").format(facility.facility_name),
		message=frappe.render_template(
			"""
			<p>A new facility registration is pending verification against the National Facility Register.</p>
			<ul>
				<li><b>Facility:</b> {{ facility_name }}</li>
				<li><b>Registration number:</b> {{ facility_registration_number }}</li>
				<li><b>Submitted by:</b> {{ contact_person_name }}</li>
			</ul>
			<p><a href="{{ url }}">Review the registration</a></p>
			""",
			{
				"facility_name": facility.facility_name,
				"facility_registration_number": facility.facility_registration_number,
				"contact_person_name": facility.contact_person_name,
				"url": frappe.utils.get_url(f"/app/facility/{facility.name}"),
			},
		),
		reference_doctype=facility.doctype,
		reference_name=facility.name,
	)


def notify_rejection(facility):
	if not facility.contact_person_email:
		return

	frappe.sendmail(
		recipients=[facility.contact_person_email],
		subject=_("Your facility registration was not approved"),
		message=frappe.render_template(
			"""
			<p>Dear {{ contact_person_name }},</p>
			<p>Your registration for <b>{{ facility_name }}</b> was not approved.</p>
			<p><b>Reason:</b> {{ reason }}</p>
			<p>You may correct the details above and resubmit the registration.</p>
			""",
			{
				"contact_person_name": facility.contact_person_name,
				"facility_name": facility.facility_name,
				"reason": facility.rejection_reason,
			},
		),
		reference_doctype=facility.doctype,
		reference_name=facility.name,
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

	facility.db_set("facility_user", user_name)
	facility.db_set("account_status", "Pending Activation")

	user_doc = frappe.get_doc("User", user_name)
	user_doc.validate_reset_password()
	user_doc._reset_password(send_email=True)


def on_facility_update(doc, method=None):
	"""Dispatch a notification off the Facility's workflow state on each transition."""
	if not doc.has_value_changed("workflow_state"):
		return

	if doc.workflow_state == "Registration Officer":
		notify_registration_officer(doc)
	elif doc.workflow_state == "Rejected":
		notify_rejection(doc)
	elif doc.workflow_state == "Approved":
		provision_facility_account(doc)


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
