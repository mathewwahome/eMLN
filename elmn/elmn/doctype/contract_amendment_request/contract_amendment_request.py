# Copyright (c) 2026, IntelliSOFT Consulting and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import now_datetime

OVERRIDE_ROLES = {"System Manager"}


class ContractAmendmentRequest(Document):
	def validate(self):
		if self.status == "Rejected" and not self.review_comment:
			frappe.throw(_("A rejection reason is required when rejecting a request."))
		if (
			not self.is_new()
			and self.status != "Pending Approval"
			and not set(frappe.get_roles(frappe.session.user)) & OVERRIDE_ROLES
		):
			frappe.throw(
				_("This request has already been reviewed and can no longer be edited."),
				frappe.PermissionError,
			)

	def _require_approver_access(self):
		if self.status != "Pending Approval":
			frappe.throw(_("This request is not pending approval."))
		roles = set(frappe.get_roles(frappe.session.user))
		if not (roles & ({self.required_approval_role} | OVERRIDE_ROLES)):
			frappe.throw(
				_("Only a {0} can approve this amendment.").format(self.required_approval_role),
				frappe.PermissionError,
			)

	@frappe.whitelist()
	def approve(self, comment=None):
		self._require_approver_access()

		source = frappe.get_doc("Contract", self.contract)
		new_contract = frappe.copy_doc(source)

		for row in self.changes:
			if row.fieldname == "commodity_scope":
				categories = [c.strip() for c in (row.new_value or "").split(",") if c.strip()]
				new_contract.set("commodity_scope", [{"commodity_category": c} for c in categories])
			else:
				new_contract.set(row.fieldname, row.new_value)

		new_contract.renewed_from = source.name
		new_contract.superseded_by = None
		new_contract.vendor_contract_status = "Draft"
		new_contract.expiry_alert_sent = 0
		new_contract.expiry_escalation_sent = 0
		new_contract.amended_from = None

		new_contract.flags.ignore_permissions = True
		new_contract.insert()
		new_contract.submit()

		self.db_set(
			{
				"status": "Approved",
				"reviewed_by": frappe.session.user,
				"reviewed_on": now_datetime(),
				"review_comment": comment,
				"new_contract": new_contract.name,
			}
		)
		self.reload()
		self._notify_vendor_of_amendment(new_contract)

	@frappe.whitelist()
	def reject(self, reason):
		if not reason:
			frappe.throw(_("A rejection reason is required."))
		self._require_approver_access()

		self.db_set(
			{
				"status": "Rejected",
				"reviewed_by": frappe.session.user,
				"reviewed_on": now_datetime(),
				"review_comment": reason,
			}
		)
		self.reload()
		self._notify_requester_rejected(reason)

	def _notify_vendor_of_amendment(self, new_contract):
		from elmn.api.emails import send_templated_email
		from elmn.api.notification import create_notification_log

		if not new_contract.vendor:
			return

		supplier = frappe.get_doc("Supplier", new_contract.vendor)
		vendor_user, email = (None, None)
		if supplier.vendor_application:
			vendor_user, email = frappe.db.get_value(
				"Vendor Application",
				supplier.vendor_application,
				["vendor_user", "primary_contact_email"],
			) or (None, None)

		if not email:
			return

		subject = _("Your contract has been amended: {0}").format(supplier.supplier_name)

		send_templated_email(
			"contract_amendment_approved",
			[email],
			{
				"supplier_name": supplier.supplier_name,
				"contract_id": new_contract.name,
				"previous_contract_id": self.contract,
				"reason": self.reason,
				"changes": [
					{"label": row.field_label, "old": row.old_value, "new": row.new_value}
					for row in self.changes
				],
			},
			default_subject=subject,
			reference_doctype=new_contract.doctype,
			reference_name=new_contract.name,
		)
		if vendor_user:
			create_notification_log([vendor_user], subject, new_contract)

	def _notify_requester_rejected(self, reason):
		from elmn.api.emails import send_templated_email
		from elmn.api.notification import create_notification_log

		if not self.requested_by:
			return

		subject = _("Contract amendment request rejected: {0}").format(self.contract)

		send_templated_email(
			"contract_amendment_rejected",
			[self.requested_by],
			{"contract_id": self.contract, "reason": reason},
			default_subject=subject,
			reference_doctype=self.doctype,
			reference_name=self.name,
		)
		create_notification_log([self.requested_by], subject, self)
