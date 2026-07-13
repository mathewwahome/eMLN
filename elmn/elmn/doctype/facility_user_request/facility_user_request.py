# Copyright (c) 2026, IntelliSOFT Consulting and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import now_datetime, validate_email_address

from elmn.api.emails import send_templated_email

PRIVILEGED_ROLES = ("System Manager", "Administrator")


class FacilityUserRequest(Document):
	def validate(self):
		validate_email_address(self.contact_email, throw=True)

		if self.status == "Rejected" and not self.rejection_reason:
			frappe.throw(_("Rejection reason is mandatory when rejecting a request."))

		if self.is_new():
			existing = frappe.db.exists(
				"Facility User Request",
				{"contact_email": self.contact_email, "status": "Pending"},
			)
			if existing:
				frappe.throw(
					_("A pending request for {0} already exists ({1}).").format(
						self.contact_email, existing
					)
				)

	def before_insert(self):
		user_roles = frappe.get_roles(frappe.session.user)
		if any(role in user_roles for role in PRIVILEGED_ROLES):
			return

		own_facility = frappe.db.get_value("User", frappe.session.user, "custom_facility")
		if not own_facility:
			frappe.throw(_("You are not linked to a Facility. Please contact MLN."))
		self.facility = own_facility

	def after_insert(self):
		from elmn.api.notification.facility import _create_notification_log, _users_with_role

		users = _users_with_role("Administrator") or _users_with_role("System Manager")
		if not users:
			frappe.logger().warning(f"Facility User Request {self.name}: no Administrator to notify")
			return

		recipients = [u.email for u in users if u.email]
		subject = _("New facility staff account request: {0}").format(self.requested_user_name)

		send_templated_email(
			"facility_user_request_pending",
			recipients,
			{
				"facility": self.facility,
				"requested_user_name": self.requested_user_name,
				"requested_role": self.requested_role,
				"contact_email": self.contact_email,
				"url": frappe.utils.get_url(f"/app/facility-user-request/{self.name}"),
			},
			default_subject=subject,
			reference_doctype=self.doctype,
			reference_name=self.name,
		)
		_create_notification_log([u.name for u in users], subject, self)

	@frappe.whitelist()
	def approve(self):
		if self.status != "Pending":
			frappe.throw(_("Only pending requests can be approved."))

		user_name = frappe.db.get_value("User", self.contact_email, "name")
		if not user_name:
			user = frappe.get_doc(
				{
					"doctype": "User",
					"email": self.contact_email,
					"first_name": self.requested_user_name,
					"user_type": "System User",
					"send_welcome_email": 0,
				}
			)
			user.append("roles", {"role": self.requested_role})
			user.insert(ignore_permissions=True)
			user_name = user.name
		else:
			user = frappe.get_doc("User", user_name)
			if not any(r.role == self.requested_role for r in user.roles):
				user.append("roles", {"role": self.requested_role})
				user.save(ignore_permissions=True)

		frappe.db.set_value("User", user_name, "custom_facility", self.facility)

		frappe.db.set_value(
			self.doctype,
			self.name,
			{
				"status": "Approved",
				"new_user": user_name,
				"reviewed_by": frappe.session.user,
				"reviewed_on": now_datetime(),
			},
		)
		self.reload()

		from elmn.api.notification.facility import send_activation_email

		user_doc = frappe.get_doc("User", user_name)
		send_activation_email(user_doc, self.requested_user_name, self.facility, self.doctype, self.name)

	@frappe.whitelist()
	def reject(self, reason):
		if self.status != "Pending":
			frappe.throw(_("Only pending requests can be rejected."))
		if not reason:
			frappe.throw(_("A rejection reason is required."))

		frappe.db.set_value(
			self.doctype,
			self.name,
			{
				"status": "Rejected",
				"rejection_reason": reason,
				"reviewed_by": frappe.session.user,
				"reviewed_on": now_datetime(),
			},
		)
		self.reload()

		send_templated_email(
			"facility_user_request_rejected",
			[self.owner],
			{
				"requested_user_name": self.requested_user_name,
				"facility": self.facility,
				"reason": reason,
			},
			default_subject=_("Your staff account request for {0} was not approved").format(
				self.requested_user_name
			),
			reference_doctype=self.doctype,
			reference_name=self.name,
		)
