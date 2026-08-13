# Copyright (c) 2026, IntelliSOFT Consulting and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document


class ContractAmendmentSettings(Document):
	def validate(self):
		if not (0 < self.major_value_change_threshold_percent <= 100):
			frappe.throw(_("The major value change threshold must be between 0 and 100%."))
