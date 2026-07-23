# Copyright (c) 2026, IntelliSOFT Consulting and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import now_datetime


class VendorRFI(Document):
	def before_insert(self):
		application_status = frappe.db.get_value("Vendor Application", self.vendor_application, "status")
		if application_status != "Under Review":
			frappe.throw(
				_("A Request for Information can only be raised while the application is Under Review.")
			)

		self.response_token = frappe.generate_hash(length=32)
		self.raised_by = frappe.session.user
		self.raised_on = now_datetime()

	def after_insert(self):
		frappe.db.set_value("Vendor Application", self.vendor_application, "status", "Awaiting Vendor Response")

		from elmn.api.notification.vendor import notify_rfi_raised

		notify_rfi_raised(self)

	def mark_responded(self):
		for row in self.items:
			row.responded = bool(row.response_text or row.response_attachment)

		self.status = "Responded"
		self.responded_on = now_datetime()
		self.save(ignore_permissions=True)

		frappe.db.set_value("Vendor Application", self.vendor_application, "status", "Under Review")

		from elmn.api.notification.vendor import notify_rfi_responded

		notify_rfi_responded(self)
