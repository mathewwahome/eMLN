# Copyright (c) 2026, IntelliSOFT Consulting and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document


class ContractAlertSettings(Document):
	def validate(self):
		if self.expiring_soon_lead_days <= 0 or self.escalation_lead_days <= 0:
			frappe.throw(_("Lead times must be greater than zero."))
		if self.escalation_lead_days >= self.expiring_soon_lead_days:
			frappe.throw(
				_("The escalation alert must fire closer to expiry than the Expiring Soon threshold.")
			)
