# Copyright (c) 2026, IntelliSOFT Consulting and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document


class Facility(Document):
	def validate(self):
		if self.workflow_state == "Rejected" and not self.rejection_reason:
			frappe.throw(_("Rejection reason is mandatory when rejecting a facility registration."))
