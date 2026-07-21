# Copyright (c) 2026, IntelliSOFT Consulting and contributors
# For license information, please see license.txt

import json

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import add_days, getdate, now_datetime, today

from elmn.api.emails import send_templated_email
from elmn.api.notification.facility import _create_notification_log, _users_with_role

CLINICAL_ROLE = "Clinical/Pharmacy Reviewer"
PROCUREMENT_ROLE = "Procurement Officer"
OVERRIDE_ROLES = ("System Manager",)

ADD_NEW_MANDATORY = (
	"item_code",
	"item_name",
	"item_group",
	"stock_uom",
	"pack_size",
	"description",
	"strength_dosage_form",
	"specifications",
	"default_unit_price",
	"min_order_qty",
	"max_order_qty",
	"supplier",
)
UPDATE_EDITABLE_FIELDS = (
	"item_name",
	"description",
	"pack_size",
	"storage_requirements",
	"strength_dosage_form",
	"specifications",
	"default_unit_price",
	"min_order_qty",
	"max_order_qty",
	"supplier",
)


class CommodityChangeRequest(Document):
	def validate(self):
		if self.status == "Rejected" and not self.rejection_reason:
			frappe.throw(_("Rejection reason is mandatory when rejecting a request."))

		if self.change_type != "Add New" and not self.item:
			frappe.throw(_("Item is required for this change type."))

		if self.change_type == "Add New" and self.is_new():
			if frappe.db.exists("Item", self.item_code):
				frappe.throw(_("A commodity with code {0} already exists.").format(self.item_code))
			return

		if self.status not in ("Draft",) and not self._is_privileged_editor():
			frappe.throw(_("This request can no longer be edited."), frappe.PermissionError)

	def _is_privileged_editor(self):
		return bool(set(frappe.get_roles(frappe.session.user)) & set(OVERRIDE_ROLES))

	def _label(self):
		return self.item_name or self.item_code or self.item

	def _run_as_admin(self, fn):
		current_user = frappe.session.user
		frappe.set_user("Administrator")
		try:
			return fn()
		finally:
			frappe.set_user(current_user)

	# ---------- submission ----------

	@frappe.whitelist()
	def submit_for_approval(self):
		if self.status != "Draft":
			frappe.throw(_("Only a draft request can be submitted for approval."))

		self._check_mandatory_fields()

		updates = {"status": "Pending Clinical Review"}
		if self.change_type == "Update":
			updates["before_snapshot"], updates["comparison_summary"] = self._build_comparison()

		self.db_set(updates)
		self.reload()

		self._notify_role(
			CLINICAL_ROLE,
			_("Commodity change pending clinical review: {0}").format(self._label()),
		)

	def _check_mandatory_fields(self):
		if self.change_type == "Add New":
			missing = [f for f in ADD_NEW_MANDATORY if not self.get(f)]
		elif self.change_type == "Suspend":
			missing = [] if self.suspension_reason else ["suspension_reason"]
		elif self.change_type == "Retire":
			missing = [f for f in ("retirement_reason", "effective_date") if not self.get(f)]
		else:
			missing = []

		if missing:
			labels = [self.meta.get_field(f).label for f in missing]
			frappe.throw(_("Please complete all mandatory fields: {0}").format(", ".join(labels)))

	def _build_comparison(self):
		item = frappe.get_doc("Item", self.item)
		before = {f: item.get(f) for f in UPDATE_EDITABLE_FIELDS}

		rows = []
		for fieldname in UPDATE_EDITABLE_FIELDS:
			new_value = self.get(fieldname)
			if new_value in (None, ""):
				continue
			old_value = before.get(fieldname)
			label = self.meta.get_field(fieldname).label
			highlight = " style='background:#fff3cd'" if str(old_value or "") != str(new_value) else ""
			rows.append(
				"<tr><td>{0}</td><td{3}>{1}</td><td{3}>{2}</td></tr>".format(
					label,
					frappe.utils.escape_html(str(old_value or "")),
					frappe.utils.escape_html(str(new_value)),
					highlight,
				)
			)

		summary = (
			"<table class='table table-bordered'><thead><tr>"
			"<th>Field</th><th>Current</th><th>Proposed</th></tr></thead>"
			"<tbody>{0}</tbody></table>"
		).format("".join(rows))
		return json.dumps(before), summary

	def _notify_role(self, role, subject):
		users = _users_with_role(role)
		if not users:
			frappe.logger().warning(f"Commodity Change Request {self.name}: no {role} to notify")
			return

		recipients = [u.email for u in users if u.email]
		send_templated_email(
			"commodity_change_request_pending",
			recipients,
			{
				"item_name": self._label(),
				"item_code": self.item_code or self.item,
				"change_type": self.change_type,
				"requested_by": self.owner,
				"url": frappe.utils.get_url(f"/app/commodity-change-request/{self.name}"),
			},
			default_subject=subject,
		)
		_create_notification_log([u.name for u in users], subject, self)

	# ---------- clinical stage ----------

	@frappe.whitelist()
	def clinical_approve(self, comment=None):
		self._require_stage(CLINICAL_ROLE, "Pending Clinical Review")
		self.db_set(
			{
				"clinical_reviewed_by": frappe.session.user,
				"clinical_reviewed_on": now_datetime(),
				"clinical_comment": comment,
				"status": "Pending Procurement Review",
			}
		)
		self.reload()
		self._notify_role(
			PROCUREMENT_ROLE,
			_("Commodity change pending procurement review: {0}").format(self._label()),
		)

	@frappe.whitelist()
	def clinical_reject(self, reason):
		self._require_stage(CLINICAL_ROLE, "Pending Clinical Review")
		self._reject(reason, "Clinical")

	# ---------- procurement stage ----------

	@frappe.whitelist()
	def procurement_approve(self, comment=None):
		self._require_stage(PROCUREMENT_ROLE, "Pending Procurement Review")
		self.db_set(
			{
				"procurement_reviewed_by": frappe.session.user,
				"procurement_reviewed_on": now_datetime(),
				"procurement_comment": comment,
				"status": "Approved",
			}
		)
		self.reload()
		self._apply_change()
		self._notify_requester(
			"commodity_change_request_approved",
			_("Your commodity change request was approved: {0}").format(self._label()),
		)

	@frappe.whitelist()
	def procurement_reject(self, reason):
		self._require_stage(PROCUREMENT_ROLE, "Pending Procurement Review")
		self._reject(reason, "Procurement")

	def _require_stage(self, role, required_status):
		if self.status != required_status:
			frappe.throw(_("This request is not at the {0} stage.").format(required_status))
		if not set(frappe.get_roles(frappe.session.user)) & ({role} | set(OVERRIDE_ROLES)):
			frappe.throw(_("You are not permitted to act on this request."), frappe.PermissionError)

	def _reject(self, reason, stage):
		if not reason:
			frappe.throw(_("A rejection reason is required."))

		self.db_set(
			{
				"status": "Rejected",
				"rejected_by": frappe.session.user,
				"rejected_on": now_datetime(),
				"rejected_at_stage": stage,
				"rejection_reason": reason,
			}
		)
		self.reload()
		self._notify_requester(
			"commodity_change_request_rejected",
			_("Your commodity change request was not approved: {0}").format(self._label()),
			extra_args={"reason": reason},
		)

	# ---------- post-rejection ----------

	@frappe.whitelist()
	def resubmit(self):
		if self.status != "Rejected":
			frappe.throw(_("Only a rejected request can be resubmitted."))

		self.db_set(
			{
				"status": "Draft",
				"rejected_by": None,
				"rejected_on": None,
				"rejected_at_stage": None,
				"rejection_reason": None,
				"clinical_reviewed_by": None,
				"clinical_reviewed_on": None,
				"clinical_comment": None,
				"procurement_reviewed_by": None,
				"procurement_reviewed_on": None,
				"procurement_comment": None,
			}
		)
		self.reload()

	@frappe.whitelist()
	def close_unresolved(self, note=None):
		if self.status != "Rejected":
			frappe.throw(_("Only a rejected request can be closed."))
		self.db_set({"status": "Closed - Unresolved", "closure_note": note})

	# ---------- applying the change ----------

	def _apply_change(self):
		if self.change_type == "Add New":
			item = self._run_as_admin(self._insert_item)
			self.db_set("item", item.name)
		elif self.change_type == "Update":
			self._run_as_admin(self._apply_update)
		elif self.change_type == "Suspend":
			self._run_as_admin(self._apply_suspend)
		elif self.change_type == "Retire":
			self._run_as_admin(self._apply_retire)

	def _insert_item(self):
		item = frappe.get_doc(
			{
				"doctype": "Item",
				"item_code": self.item_code,
				"item_name": self.item_name,
				"item_group": self.item_group,
				"stock_uom": self.stock_uom,
				"pack_size": self.pack_size,
				"therapeutic_class": self.therapeutic_class,
				"brand": self.brand,
				"description": self.description,
				"strength_dosage_form": self.strength_dosage_form,
				"specifications": self.specifications,
				"storage_requirements": self.storage_requirements,
				"standard_rate": self.default_unit_price,
				"min_order_qty": self.min_order_qty,
				"max_order_qty": self.max_order_qty,
				"minimum_shelf_life_at_delivery": self.minimum_shelf_life_at_delivery,
				"price_tax_treatment": self.price_tax_treatment,
				"status_label": "Active",
				"is_stock_item": 1,
			}
		)
		if self.supplier:
			item.append("supplier_items", {"supplier": self.supplier})
		item.insert(ignore_permissions=True)
		return item

	def _apply_update(self):
		item = frappe.get_doc("Item", self.item)
		for fieldname in UPDATE_EDITABLE_FIELDS:
			value = self.get(fieldname)
			if value not in (None, ""):
				item.set(fieldname, value)
		item.save(ignore_permissions=True)

	def _apply_suspend(self):
		from elmn.api.catalogue import suspend_item

		suspend_item(self.item, self.suspension_reason)
		if self.expected_duration_days:
			frappe.db.set_value(
				"Item", self.item, "expected_reactivation_date", add_days(today(), self.expected_duration_days)
			)

	def _apply_retire(self):
		if self.effective_date and getdate(self.effective_date) > getdate(today()):
			frappe.db.set_value(
				"Item",
				self.item,
				{"scheduled_retirement_date": self.effective_date, "scheduled_retirement_ccr": self.name},
			)
			return
		self._retire_now()

	def _retire_now(self):
		from elmn.api.catalogue import retire_item

		if self.replacement_item:
			frappe.db.set_value("Item", self.item, "replacement_item", self.replacement_item)
		retire_item(self.item, self.retirement_reason)

	def _notify_requester(self, template_name, default_subject, extra_args=None):
		if not self.owner or not frappe.utils.validate_email_address(self.owner):
			return

		args = {
			"item_name": self._label(),
			"item_code": self.item_code or self.item,
		}
		if extra_args:
			args.update(extra_args)

		send_templated_email(
			template_name,
			[self.owner],
			args,
			default_subject=default_subject,
			reference_doctype=self.doctype,
			reference_name=self.name,
		)
		_create_notification_log([self.owner], default_subject, self)
