# Copyright (c) 2026, IntelliSOFT Consulting and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import now_datetime

REVIEWER_ROLES = {"Clinical/Pharmacy Reviewer", "System Manager"}


def _require_reviewer_access():
	if not set(frappe.get_roles(frappe.session.user)) & REVIEWER_ROLES:
		frappe.throw(_("Not permitted"), frappe.PermissionError)


class VendorProfileChangeRequest(Document):
	@frappe.whitelist()
	def approve(self):
		_require_reviewer_access()

		if self.status != "Pending MLN Review":
			frappe.throw(_("Only a request that is Pending MLN Review can be approved."))

		supplier = frappe.get_doc("Supplier", self.supplier)
		for row in self.changes:
			if row.fieldname == "primary_commodity_categories":
				categories = [c.strip() for c in (row.new_value or "").split(",") if c.strip()]
				supplier.set(
					"primary_commodity_categories", [{"commodity_category": c} for c in categories]
				)
			else:
				supplier.set(row.fieldname, row.new_value)
		supplier.save(ignore_permissions=True)

		self.status = "Approved"
		self.reviewed_by = frappe.session.user
		self.reviewed_on = now_datetime()
		self.save(ignore_permissions=True)

		self._notify_vendor(
			"vendor_profile_change_request_approved",
			_("Your vendor profile change request has been approved: {0}"),
		)

	@frappe.whitelist()
	def reject(self, reason=None):
		_require_reviewer_access()

		if self.status != "Pending MLN Review":
			frappe.throw(_("Only a request that is Pending MLN Review can be rejected."))

		self.status = "Rejected"
		self.review_comment = reason
		self.reviewed_by = frappe.session.user
		self.reviewed_on = now_datetime()
		self.save(ignore_permissions=True)

		self._notify_vendor(
			"vendor_profile_change_request_rejected",
			_("Your vendor profile change request was not approved: {0}"),
			extra={"reason": reason},
		)

	def _notify_vendor(self, template, subject_template, extra=None):
		from elmn.api.emails import send_templated_email
		from elmn.api.notification import create_notification_log

		supplier = frappe.get_doc("Supplier", self.supplier)
		vendor_user, email = (None, None)
		if supplier.vendor_application:
			vendor_user, email = frappe.db.get_value(
				"Vendor Application",
				supplier.vendor_application,
				["vendor_user", "primary_contact_email"],
			) or (None, None)

		if not email:
			return

		subject = subject_template.format(supplier.supplier_name)
		args = {
			"supplier_name": supplier.supplier_name,
			"changes": [
				{"label": row.field_label, "old": row.old_value, "new": row.new_value}
				for row in self.changes
			],
		}
		if extra:
			args.update(extra)

		send_templated_email(
			template,
			[email],
			args,
			default_subject=subject,
			reference_doctype=self.doctype,
			reference_name=self.name,
		)
		if vendor_user:
			create_notification_log([vendor_user], subject, self)
