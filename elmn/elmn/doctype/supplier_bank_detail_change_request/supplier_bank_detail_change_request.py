# Copyright (c) 2026, IntelliSOFT Consulting and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import now_datetime

REQUESTER_ROLES = {"Finance Officer", "Head of Finance/Finance Approver", "System Manager"}
APPROVER_ROLES = {"Finance Officer", "Head of Finance/Finance Approver", "System Manager"}
NOTIFY_APPROVER_ROLES = {"Finance Officer", "Head of Finance/Finance Approver"}
OVERRIDE_ROLES = {"System Manager"}

BANK_FIELDS = ("bank_name", "branch_name", "account_name", "account_number", "swift_bic_code")


def _require_requester_access():
	if not set(frappe.get_roles(frappe.session.user)) & REQUESTER_ROLES:
		frappe.throw(_("You do not have access to request banking detail changes."), frappe.PermissionError)


class SupplierBankDetailChangeRequest(Document):
	def validate(self):
		if self.is_new():
			_require_requester_access()
			self.requested_by = frappe.session.user
			self.requested_on = now_datetime()
			self.status = "Pending Second Approval"

			supplier = frappe.get_doc("Supplier", self.supplier)
			for fieldname in BANK_FIELDS:
				self.set(f"current_{fieldname}", supplier.get(fieldname))

			if all(
				(self.get(f"new_{f}") or "") == (self.get(f"current_{f}") or "") for f in BANK_FIELDS
			):
				frappe.throw(_("No banking detail changes were submitted."))
		elif self.status != "Pending Second Approval" and not set(
			frappe.get_roles(frappe.session.user)
		) & OVERRIDE_ROLES:
			frappe.throw(_("This request has already been reviewed and can no longer be edited."))

	def after_insert(self):
		self._notify_approvers()

	@frappe.whitelist()
	def approve(self, comment=None):
		self._require_second_approver()

		supplier = frappe.get_doc("Supplier", self.supplier)
		for fieldname in BANK_FIELDS:
			supplier.set(fieldname, self.get(f"new_{fieldname}"))
		supplier.save(ignore_permissions=True)

		self.db_set(
			{
				"status": "Approved",
				"reviewed_by": frappe.session.user,
				"reviewed_on": now_datetime(),
				"review_comment": comment,
			}
		)
		self.reload()
		self._notify_requester(
			"supplier_bank_detail_change_approved",
			_("Your banking detail change for {0} has been approved and applied.").format(
				supplier.supplier_name
			),
		)

	@frappe.whitelist()
	def reject(self, reason):
		if not reason:
			frappe.throw(_("A rejection reason is required."))
		self._require_second_approver()

		self.db_set(
			{
				"status": "Rejected",
				"reviewed_by": frappe.session.user,
				"reviewed_on": now_datetime(),
				"review_comment": reason,
			}
		)
		self.reload()
		self._notify_requester(
			"supplier_bank_detail_change_rejected",
			_("Your banking detail change request was not approved."),
			extra_args={"reason": reason},
		)

	def _require_second_approver(self):
		if self.status != "Pending Second Approval":
			frappe.throw(_("This request is not pending approval."))
		if not set(frappe.get_roles(frappe.session.user)) & APPROVER_ROLES:
			frappe.throw(_("You are not permitted to review this request."), frappe.PermissionError)
		if frappe.session.user == self.requested_by:
			frappe.throw(
				_(
					"Banking detail changes require approval from a second Finance Officer "
					"- you cannot approve your own request."
				),
				frappe.PermissionError,
			)

	def _notify_approvers(self):
		from elmn.api.emails import send_templated_email
		from elmn.api.notification import create_notification_log, users_with_role

		seen = {}
		for role in NOTIFY_APPROVER_ROLES:
			for user in users_with_role(role):
				if user.name != self.requested_by:
					seen[user.name] = user
		users = list(seen.values())
		if not users:
			return

		supplier_name = frappe.db.get_value("Supplier", self.supplier, "supplier_name")
		subject = _("Banking detail change pending second approval: {0}").format(supplier_name)

		send_templated_email(
			"supplier_bank_detail_change_pending",
			[u.email for u in users if u.email],
			{
				"supplier_name": supplier_name,
				"requested_by": self.requested_by,
				"bank_name": self.new_bank_name,
				"account_number": self.new_account_number,
				"url": frappe.utils.get_url(f"/app/supplier-bank-detail-change-request/{self.name}"),
			},
			default_subject=subject,
			reference_doctype=self.doctype,
			reference_name=self.name,
		)
		create_notification_log([u.name for u in users], subject, self)

	def _notify_requester(self, template, default_subject, extra_args=None):
		from elmn.api.emails import send_templated_email
		from elmn.api.notification import create_notification_log

		if not self.requested_by:
			return

		args = {
			"supplier_name": frappe.db.get_value("Supplier", self.supplier, "supplier_name"),
			"bank_name": self.new_bank_name,
			"account_number": self.new_account_number,
		}
		if extra_args:
			args.update(extra_args)

		send_templated_email(
			template,
			[self.requested_by],
			args,
			default_subject=default_subject,
			reference_doctype=self.doctype,
			reference_name=self.name,
		)
		create_notification_log([self.requested_by], default_subject, self)
