# Copyright (c) 2026, IntelliSOFT Consulting and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import now_datetime

OVERRIDE_ROLES = {"System Manager"}


class ContractTerminationRequest(Document):
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
				_("Only a {0} can approve this termination.").format(self.required_approval_role),
				frappe.PermissionError,
			)

	@frappe.whitelist()
	def approve(self, comment=None):
		self._require_approver_access()

		contract = frappe.get_doc("Contract", self.contract)
		contract.vendor_contract_status = "Terminated"
		contract.flags.ignore_permissions = True
		contract.save()
		contract.add_comment(
			"Info", _("Terminated ({0}): {1}").format(self.termination_reason_category, self.reason)
		)

		flagged = self._flag_purchase_orders(contract)

		self.db_set(
			{
				"status": "Approved",
				"reviewed_by": frappe.session.user,
				"reviewed_on": now_datetime(),
				"review_comment": comment,
				"flagged_purchase_order_count": len(flagged),
				"flagged_purchase_orders": ", ".join(flagged),
			}
		)
		self.reload()
		self._notify_vendor_of_termination()
		if flagged:
			self._notify_flagged_purchase_orders(flagged)

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

	def _flag_purchase_orders(self, contract):
		from elmn.api.catalogue import ACTIVE_PURCHASE_ORDER_STATUSES

		reason = _("Contract {0} with this vendor was terminated ({1}): {2}").format(
			contract.name, self.termination_reason_category, self.reason
		)

		po_names = frappe.get_all(
			"Purchase Order",
			filters=[
				["Purchase Order", "contract", "=", contract.name],
				["Purchase Order", "docstatus", "=", 1],
				["Purchase Order", "status", "in", ACTIVE_PURCHASE_ORDER_STATUSES],
			],
			pluck="name",
		)
		draft_names = frappe.get_all(
			"Purchase Order",
			filters={"contract": contract.name, "docstatus": 0},
			pluck="name",
		)
		po_names = list(dict.fromkeys(po_names + draft_names))

		for po_name in po_names:
			frappe.db.set_value(
				"Purchase Order", po_name, {"flagged_for_review": 1, "flag_reason": reason}
			)
			frappe.get_doc("Purchase Order", po_name).add_comment("Info", reason)

		return po_names

	def _notify_vendor_of_termination(self):
		from elmn.api.emails import send_templated_email
		from elmn.api.notification import create_notification_log

		if not self.vendor:
			return

		supplier = frappe.get_doc("Supplier", self.vendor)
		vendor_user, email = (None, None)
		if supplier.vendor_application:
			vendor_user, email = frappe.db.get_value(
				"Vendor Application",
				supplier.vendor_application,
				["vendor_user", "primary_contact_email"],
			) or (None, None)

		if not email:
			return

		subject = _("Your contract has been terminated: {0}").format(self.contract)

		send_templated_email(
			"contract_termination_notice",
			[email],
			{
				"supplier_name": supplier.supplier_name,
				"contract_id": self.contract,
				"reason_category": self.termination_reason_category,
				"reason": self.reason,
			},
			default_subject=subject,
			reference_doctype=self.doctype,
			reference_name=self.name,
		)
		if vendor_user:
			create_notification_log([vendor_user], subject, self)

	def _notify_flagged_purchase_orders(self, po_names):
		from elmn.api.emails import send_templated_email
		from elmn.api.notification import create_notification_log, users_with_role

		users = users_with_role("Procurement Officer") or users_with_role(
			"Head of Procurement/ Procurement Approver"
		)
		if not users:
			frappe.logger().warning(
				f"Contract Termination Request {self.name}: no Procurement Officer to notify about "
				f"{len(po_names)} flagged purchase order(s)"
			)
			return

		subject = _("{0} purchase order(s) flagged for review: contract {1} terminated").format(
			len(po_names), self.contract
		)

		send_templated_email(
			"contract_terminated_po_flagged",
			[u.email for u in users if u.email],
			{
				"contract_id": self.contract,
				"reason": self.reason,
				"purchase_orders": [
					{"name": po_name, "url": frappe.utils.get_url(f"/app/purchase-order/{po_name}")}
					for po_name in po_names
				],
			},
			default_subject=subject,
			reference_doctype=self.doctype,
			reference_name=self.name,
		)
		create_notification_log([u.name for u in users], subject, self)

	def _notify_requester_rejected(self, reason):
		from elmn.api.emails import send_templated_email
		from elmn.api.notification import create_notification_log

		if not self.requested_by:
			return

		subject = _("Contract termination request rejected: {0}").format(self.contract)

		send_templated_email(
			"contract_termination_rejected",
			[self.requested_by],
			{"contract_id": self.contract, "reason": reason},
			default_subject=subject,
			reference_doctype=self.doctype,
			reference_name=self.name,
		)
		create_notification_log([self.requested_by], subject, self)
