# Copyright (c) 2026, IntelliSOFT Consulting and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import add_days, cint, get_url, now_datetime, sha256_hash, today, validate_email_address

from elmn.api.emails import send_templated_email

NON_RESENDABLE_STATUSES = ("Registration Started", "Registration Submitted", "Cancelled")
NON_CANCELLABLE_STATUSES = ("Registration Submitted", "Cancelled")


class FacilityInvitation(Document):
	def validate(self):
		validate_email_address(self.contact_email, throw=True)

	def after_insert(self):
		self.send_invitation()

	def send_invitation(self, is_resend=False):
		token = frappe.generate_hash()
		settings = frappe.get_single("Invitation Settings")
		expiry_days = cint(self.expiry_days_override) or cint(settings.default_expiry_days) or 14

		values = {
			"invitation_token_hash": sha256_hash(token),
			"token_generated_on": now_datetime(),
			"expiry_date": add_days(today(), expiry_days),
			"sent_on": now_datetime(),
			"status": "Resent" if is_resend else "Sent",
		}
		if is_resend:
			values["resend_count"] = cint(self.resend_count) + 1

		frappe.db.set_value(self.doctype, self.name, values)
		self.reload()

		send_templated_email(
			"invitation",
			[self.contact_email],
			{
				"facility_legal_name": self.facility_legal_name,
				"custom_message": self.custom_message,
				"link": get_url(f"/facility-registration?token={token}"),
				"expected_processing_days": settings.expected_processing_days,
				"support_email": settings.support_email,
				"support_phone": settings.support_phone,
			},
			default_subject=_("You're invited to register {0} on eLMN").format(self.facility_legal_name),
			reference_doctype=self.doctype,
			reference_name=self.name,
		)

	@frappe.whitelist()
	def resend_invitation(self):
		if self.status in NON_RESENDABLE_STATUSES:
			frappe.throw(_("This invitation can no longer be resent."))

		self.send_invitation(is_resend=True)
		self.add_comment("Info", _("Invitation resent to {0}").format(self.contact_email))

	@frappe.whitelist()
	def cancel_invitation(self, reason):
		if self.status in NON_CANCELLABLE_STATUSES:
			frappe.throw(_("This invitation can no longer be cancelled."))
		if not reason:
			frappe.throw(_("A cancellation reason is required."))

		frappe.db.set_value(
			self.doctype,
			self.name,
			{
				"status": "Cancelled",
				"cancelled_on": now_datetime(),
				"cancelled_by": frappe.session.user,
				"cancellation_reason": reason,
				"invitation_token_hash": None,
			},
		)
		self.reload()

		send_templated_email(
			"invitation_cancelled",
			[self.contact_email],
			{"facility_legal_name": self.facility_legal_name, "reason": reason},
			default_subject=_("Your invitation to register {0} has been cancelled").format(
				self.facility_legal_name
			),
			reference_doctype=self.doctype,
			reference_name=self.name,
		)
		self.add_comment("Info", _("Invitation cancelled: {0}").format(reason))


def mark_registration_submitted(invitation_name, facility_name):
	if not invitation_name:
		return

	frappe.db.set_value(
		"Facility Invitation",
		invitation_name,
		{
			"status": "Registration Submitted",
			"registration_submitted_on": now_datetime(),
			"facility": facility_name,
		},
	)
