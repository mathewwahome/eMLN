# Copyright (c) 2026, IntelliSOFT Consulting and contributors
# For license information, please see license.txt

import os
import re

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import add_days, now_datetime

ACCOUNT_NUMBER_PATTERN = re.compile(r"^[A-Za-z0-9\-]{6,34}$")
SWIFT_BIC_PATTERN = re.compile(r"^[A-Z]{6}[A-Z0-9]{2}([A-Z0-9]{3})?$")


class VendorApplication(Document):
	def validate(self):
		self.validate_commodities()
		self._validate_uploaded_files()
		self.validate_banking_details()

		if self.status == "Draft":
			self._refresh_draft_expiry()
		else:
			self._check_required_documents()

	def after_insert(self):
		if self.status == "Draft":
			from elmn.api.notification.vendor import notify_draft_saved

			notify_draft_saved(self)
		else:
			self._notify_submission()

	def on_update(self):
		previous = self.get_doc_before_save()
		if previous and previous.status == "Draft" and self.status != "Draft":
			self._notify_submission()

	def _notify_submission(self):
		from elmn.api.notification.vendor import (
			notify_admin_of_new_submission,
			notify_applicant_submission_received,
		)

		notify_applicant_submission_received(self)
		notify_admin_of_new_submission(self)

	def _refresh_draft_expiry(self):
		settings = frappe.get_single("Invitation Settings")
		if not self.draft_token:
			self.draft_token = frappe.generate_hash(length=32)
		self.draft_expires_on = add_days(now_datetime(), settings.draft_expiry_days or 14)
		self.draft_expiry_warning_sent = 0

	def validate_banking_details(self):
		if self.account_number and not ACCOUNT_NUMBER_PATTERN.match(self.account_number):
			frappe.throw(_("Account number must be 6-34 alphanumeric characters."))

		if self.swift_bic_code and not SWIFT_BIC_PATTERN.match(self.swift_bic_code.upper()):
			frappe.throw(
				_(
					"SWIFT/BIC code must be 8 or 11 characters in standard SWIFT format (e.g. AAAABBCC or AAAABBCCXXX)."
				)
			)

	def validate_commodities(self):
		for row in self.commodities:
			item_group = frappe.db.get_value("Item", row.commodity, "item_group")
			if item_group and item_group != row.commodity_category:
				frappe.throw(
					_("Row #{0}: {1} belongs to commodity category {2}, not {3}.").format(
						row.idx, row.commodity, item_group, row.commodity_category
					)
				)

	def _required_document_types(self):
		required = set(
			frappe.get_all("Vendor Document Type", filters={"is_mandatory_default": 1}, pluck="name")
		)
		if self.vendor_type:
			required.update(
				row.document_type
				for row in frappe.get_all(
					"Vendor Type Required Document",
					filters={"parenttype": "Vendor Type", "parent": self.vendor_type},
					fields=["document_type"],
				)
			)
		return required

	def _validate_uploaded_files(self):
		for row in self.documents:
			if row.attachment:
				self._validate_file(row)

	def _check_required_documents(self):
		uploaded_types = {row.document_type for row in self.documents if row.attachment}
		missing = self._required_document_types() - uploaded_types
		if missing:
			frappe.throw(
				_("Please upload the following mandatory documents before submitting: {0}").format(
					", ".join(sorted(missing))
				)
			)

	def _validate_file(self, row):
		doc_type = frappe.get_cached_doc("Vendor Document Type", row.document_type)

		extension = os.path.splitext(row.attachment)[1].lstrip(".").lower()
		allowed = [ext.strip().lower() for ext in (doc_type.allowed_file_types or "").split(",") if ext.strip()]
		if allowed and extension not in allowed:
			frappe.throw(
				_("{0}: file type .{1} is not allowed. Allowed types: {2}").format(
					doc_type.document_name, extension, ", ".join(allowed)
				)
			)

		file_size = frappe.db.get_value("File", {"file_url": row.attachment}, "file_size")
		max_size = (doc_type.max_file_size_mb or 0) * 1024 * 1024
		if file_size and max_size and file_size > max_size:
			frappe.throw(
				_("{0}: file exceeds the maximum allowed size of {1} MB.").format(
					doc_type.document_name, doc_type.max_file_size_mb
				)
			)
