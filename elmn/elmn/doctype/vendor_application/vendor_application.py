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

REVIEWER_ROLES = {"Clinical/Pharmacy Reviewer", "System Manager"}


def _require_reviewer_access():
	if not set(frappe.get_roles(frappe.session.user)) & REVIEWER_ROLES:
		frappe.throw(_("Not permitted"), frappe.PermissionError)


class VendorApplication(Document):
	def validate(self):
		self.validate_commodities()
		self._validate_uploaded_files()
		self.validate_banking_details()
		self._validate_document_review_comments()
		self._sync_document_review_status()
		self._validate_verification_notes()
		self._sync_verification_status()

		if self.status == "Draft":
			self._refresh_draft_expiry()
		else:
			self._check_required_documents()
			self._ensure_verification_checklist()

	def _validate_document_review_comments(self):
		for row in self.documents:
			if row.review_status in ("Queried", "Rejected") and not row.review_comment:
				frappe.throw(
					_("Row #{0} ({1}): a note/reason is required when marking a document {2}.").format(
						row.idx, row.document_type, row.review_status
					)
				)

	def _sync_document_review_status(self):
		statuses = [row.review_status for row in self.documents]
		if self.document_review_status == "Completed" and statuses and all(statuses):
			return
		self.document_review_status = "In Progress" if any(statuses) else "Not Started"

	@frappe.whitelist()
	def complete_document_review(self):
		_require_reviewer_access()

		missing = [row.document_type for row in self.documents if not row.review_status]
		if missing:
			frappe.throw(
				_("Please set a review status for all documents before completing the review: {0}").format(
					", ".join(missing)
				)
			)

		self.document_review_status = "Completed"
		self.document_review_completed_by = frappe.session.user
		self.document_review_completed_on = now_datetime()
		self.save()

	def _ensure_verification_checklist(self):
		if self.verifications:
			return
		for check_type in frappe.get_all(
			"Vendor Verification Check Type",
			fields=["name", "is_mandatory_default", "verification_method"],
			order_by="creation",
		):
			self.append(
				"verifications",
				{
					"check_type": check_type.name,
					"is_mandatory": check_type.is_mandatory_default,
					"verification_method": check_type.verification_method,
				},
			)

	def _validate_verification_notes(self):
		for row in self.verifications:
			if row.outcome and not row.note:
				frappe.throw(
					_("Row #{0} ({1}): a note is required when recording a verification outcome.").format(
						row.idx, row.check_type
					)
				)
			if row.outcome and not row.verified_by:
				row.verified_by = frappe.session.user
				row.verified_on = now_datetime()
			elif not row.outcome:
				row.verified_by = None
				row.verified_on = None

	def _sync_verification_status(self):
		outcomes = [row.outcome for row in self.verifications]
		if self.verification_status == "Completed" and outcomes and all(outcomes):
			return
		self.verification_status = "In Progress" if any(outcomes) else "Not Started"

	@frappe.whitelist()
	def complete_verification(self):
		_require_reviewer_access()

		mandatory_checks = {row.check_type for row in self.verifications if row.is_mandatory}
		verified_checks = {row.check_type for row in self.verifications if row.outcome}
		missing = mandatory_checks - verified_checks
		if missing:
			frappe.throw(
				_("Please record an outcome for the following mandatory checks before proceeding: {0}").format(
					", ".join(sorted(missing))
				)
			)

		self.verification_status = "Completed"
		self.verification_completed_by = frappe.session.user
		self.verification_completed_on = now_datetime()
		self.save()

	@frappe.whitelist()
	def approve(self, notes=None):
		_require_reviewer_access()

		if self.status != "Under Review":
			frappe.throw(_("Only an application that is Under Review can be approved."))

		if self.document_review_status != "Completed":
			frappe.throw(_("Document review must be completed before the application can be approved."))

		not_accepted = [row.document_type for row in self.documents if row.review_status != "Accepted"]
		if not_accepted:
			frappe.throw(
				_("All documents must be Accepted before the application can be approved: {0}").format(
					", ".join(not_accepted)
				)
			)

		if self.verification_status != "Completed":
			frappe.throw(_("Information verification must be completed before the application can be approved."))

		self.status = "Approved"
		self.approval_notes = notes
		self.approved_by = frappe.session.user
		self.approved_on = now_datetime()
		self.save(ignore_permissions=True)

		self._link_supplier_account()

	@frappe.whitelist()
	def link_supplier_account(self):
		_require_reviewer_access()

		if self.status != "Approved":
			frappe.throw(_("Only an approved vendor application can be linked to a supplier account."))

		if self.supplier and self.vendor_user:
			frappe.msgprint(_("A supplier account is already linked to this application."))
			return

		self._link_supplier_account()

	def _link_supplier_account(self):
		if not self.supplier:
			self.db_set("supplier", self._create_supplier_master())

		self._provision_vendor_account()

	def _create_supplier_master(self):
		commodity_categories = list(
			{row.commodity_category for row in self.commodities if row.commodity_category}
		)
		supplier = frappe.get_doc(
			{
				"doctype": "Supplier",
				"naming_series": "SUP-.YYYY.-",
				"supplier_name": self.legal_entity_name,
				"supplier_type": "Company",
				"country": self.country_of_incorporation,
				"tax_id": self.company_registration_number,
				"supplier_group": (
					"All Supplier Groups" if frappe.db.exists("Supplier Group", "All Supplier Groups") else None
				),
				"vendor_type": self.vendor_type,
				"vendor_status": "Active",
				"contract_status": "Active",
				"vendor_application": self.name,
				"primary_commodity_categories": [
					{"commodity_category": category} for category in commodity_categories
				],
				"documents": [
					{"document_type": row.document_type, "attachment": row.attachment}
					for row in self.documents
					if row.attachment
				],
				"bank_name": self.bank_name,
				"branch_name": self.branch_name,
				"account_name": self.account_name,
				"account_number": self.account_number,
				"swift_bic_code": self.swift_bic_code,
				"primary_contact_name": self.primary_contact_name,
				"primary_contact_phone": self.primary_contact_phone,
				"primary_contact_email": self.primary_contact_email,
				"registered_address": self.registered_address,
			}
		)
		supplier.insert(ignore_permissions=True)
		return supplier.name

	def _provision_vendor_account(self):
		if not self.primary_contact_email:
			frappe.throw(_("Primary contact email is required to activate a vendor account."))

		user_name = frappe.db.get_value("User", self.primary_contact_email, "name")
		if not user_name:
			user = frappe.get_doc(
				{
					"doctype": "User",
					"email": self.primary_contact_email,
					"first_name": self.primary_contact_name or self.legal_entity_name,
					"user_type": "System User",
					"send_welcome_email": 0,
				}
			)
			user.append("roles", {"role": "Supplier"})
			user.insert(ignore_permissions=True)
			user_name = user.name
		else:
			self._ensure_supplier_role(user_name)

		self.db_set("vendor_user", user_name)
		self._grant_supplier_access(user_name)

		from elmn.api.notification.vendor import notify_approval

		notify_approval(self, frappe.get_doc("User", user_name))

	@frappe.whitelist()
	def change_vendor_user(self, new_user):
		_require_reviewer_access()

		if self.status != "Approved" or not self.supplier:
			frappe.throw(_("The vendor account must be linked before you can change the vendor user."))

		if not frappe.db.exists("User", new_user):
			frappe.throw(_("User {0} does not exist.").format(new_user))

		if new_user == self.vendor_user:
			frappe.msgprint(_("{0} is already the vendor user for this application.").format(new_user))
			return

		old_user = self.vendor_user
		if old_user:
			frappe.db.delete(
				"User Permission", {"user": old_user, "allow": "Supplier", "for_value": self.supplier}
			)

		self._ensure_supplier_role(new_user)
		self.db_set("vendor_user", new_user)
		self._grant_supplier_access(new_user)

	def _ensure_supplier_role(self, user_name):
		if not frappe.db.exists("Has Role", {"parent": user_name, "role": "Supplier"}):
			user = frappe.get_doc("User", user_name)
			user.append("roles", {"role": "Supplier"})
			user.save(ignore_permissions=True)

	def _grant_supplier_access(self, user_name):
		if not frappe.db.exists(
			"User Permission", {"user": user_name, "allow": "Supplier", "for_value": self.supplier}
		):
			frappe.get_doc(
				{
					"doctype": "User Permission",
					"user": user_name,
					"allow": "Supplier",
					"for_value": self.supplier,
				}
			).insert(ignore_permissions=True)

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
