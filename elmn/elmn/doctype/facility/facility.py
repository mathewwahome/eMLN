# Copyright (c) 2026, IntelliSOFT Consulting and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import now_datetime

from elmn.api.emails import send_templated_email

OFFBOARDING_ROLES = {"System Manager", "Administrator"}


def _require_offboarding_access():
	if not set(frappe.get_roles(frappe.session.user)) & OFFBOARDING_ROLES:
		frappe.throw(_("Not permitted"), frappe.PermissionError)


class Facility(Document):
	def validate(self):
		if self.workflow_state == "Rejected" and not self.rejection_reason:
			frappe.throw(_("Rejection reason is mandatory when rejecting a facility registration."))

	def before_insert(self):
		if not self.invitation_token:
			return

		from elmn.api.invitation import validate_and_start_invitation

		result = validate_and_start_invitation(self.invitation_token)
		if not result.get("valid"):
			frappe.throw(result.get("message") or _("This invitation link is no longer valid."))

		invitation = frappe.get_doc("Facility Invitation", result["invitation"])
		self.invitation = invitation.name
		self.registration_source = "Invited"

		prefill = {
			"facility_level": invitation.prefill_facility_level,
			"region": invitation.prefill_region,
			"district": invitation.prefill_district,
			"physical_address": invitation.prefill_physical_address,
			"contact_person_phone": invitation.prefill_phone,
		}
		for fieldname, value in prefill.items():
			if value and not self.get(fieldname):
				self.set(fieldname, value)

		self.invitation_token = None

	def after_insert(self):
		if self.registration_source != "Invited":
			return

		from elmn.api.notification.facility import (
			notify_applicant_submission_received,
			notify_registration_officer,
		)
		from elmn.elmn.doctype.facility_invitation.facility_invitation import mark_registration_submitted

		frappe.db.set_value(
			self.doctype,
			self.name,
			{"workflow_state": "Registration Officer", "registration_status": "Pending verification"},
		)
		self.reload()

		notify_registration_officer(self)
		notify_applicant_submission_received(self)
		mark_registration_submitted(self.invitation, self.name)

	def _facility_users(self):
		return frappe.get_all("User", filters={"custom_facility": self.name}, pluck="name")

	def _set_users_enabled(self, enabled):
		for user_name in self._facility_users():
			frappe.db.set_value("User", user_name, "enabled", enabled)

	def _offboard(self, action, reason):
		_require_offboarding_access()
		if self.workflow_state != "Approved":
			frappe.throw(_("Only an approved facility can be {0}.").format(action.lower()))
		if self.operational_status != "Active":
			frappe.throw(_("This facility is already {0}.").format(self.operational_status.lower()))
		if not reason:
			frappe.throw(_("A reason is required."))

		self._set_users_enabled(0)

		for name in frappe.get_all(
			"Facility User Request",
			filters={"facility": self.name, "status": "Pending"},
			pluck="name",
		):
			frappe.get_doc("Facility User Request", name).reject(
				_("Facility was {0}.").format(action.lower())
			)

		frappe.db.set_value(
			self.doctype,
			self.name,
			{
				"operational_status": action,
				"offboarding_reason": reason,
				"offboarded_by": frappe.session.user,
				"offboarded_on": now_datetime(),
			},
		)
		self.reload()

		if self.contact_person_email:
			send_templated_email(
				"facility_offboarded",
				[self.contact_person_email],
				{
					"contact_person_name": self.contact_person_name,
					"facility_name": self.facility_name,
					"action": action.lower(),
					"reason": reason,
				},
				default_subject=_("Your facility {0} has been {1}").format(
					self.facility_name, action.lower()
				),
				reference_doctype=self.doctype,
				reference_name=self.name,
			)

	@frappe.whitelist()
	def suspend(self, reason):
		self._offboard("Suspended", reason)

	@frappe.whitelist()
	def remove(self, reason):
		self._offboard("Removed", reason)

	@frappe.whitelist()
	def reactivate(self):
		_require_offboarding_access()
		if self.operational_status not in ("Suspended", "Removed"):
			frappe.throw(_("This facility is not suspended or removed."))

		self._set_users_enabled(1)

		frappe.db.set_value(
			self.doctype,
			self.name,
			{
				"operational_status": "Active",
				"offboarding_reason": None,
				"offboarded_by": None,
				"offboarded_on": None,
			},
		)
		self.reload()

		if self.contact_person_email:
			send_templated_email(
				"facility_reactivated",
				[self.contact_person_email],
				{
					"contact_person_name": self.contact_person_name,
					"facility_name": self.facility_name,
				},
				default_subject=_("Your facility {0} has been reactivated").format(self.facility_name),
				reference_doctype=self.doctype,
				reference_name=self.name,
			)
