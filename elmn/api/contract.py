import frappe
from frappe import _
from frappe.utils import add_days, cint, formatdate, getdate, now_datetime, today

TERMINAL_STATUSES = {"Terminated", "Superseded"}
DEFAULT_EXPIRING_SOON_DAYS = 30
DEFAULT_ESCALATION_DAYS = 7
RENEWABLE_STATUSES = {"Expiring Soon", "Expired"}
RENEWAL_ROLES = {"Procurement Officer", "Purchase Manager", "System Manager"}
AMENDMENT_ROLES = {"Procurement Officer", "Purchase Manager", "System Manager"}
AMENDABLE_FIELDS = {
	"contract_value": "Contract value",
	"end_date": "End date",
	"contract_terms": "Contract terms",
	"commodity_scope": "Commodity scope",
}
TERMINATION_ROLES = {"Procurement Officer", "Purchase Manager", "System Manager"}


def _alert_settings():
	settings = frappe.get_cached_doc("Contract Alert Settings")
	lead_days = cint(settings.expiring_soon_lead_days) or DEFAULT_EXPIRING_SOON_DAYS
	escalation_days = cint(settings.escalation_lead_days) or DEFAULT_ESCALATION_DAYS
	return lead_days, escalation_days


def sync_vendor_fields(doc, method=None):
	doc.vendor = doc.party_name if doc.party_type == "Supplier" else None
	doc.vendor_contract_status = _compute_status(doc)

	before = doc.get_doc_before_save()
	if before and before.end_date != doc.end_date:
		doc.expiry_alert_sent = 0
		doc.expiry_escalation_sent = 0


def handle_cancel(doc, method=None):
	if doc.vendor_contract_status not in TERMINAL_STATUSES:
		doc.db_set("vendor_contract_status", "Terminated")


def handle_submit(doc, method=None):
	doc.db_set({"activated_on": now_datetime(), "activated_by": frappe.session.user})

	if doc.renewed_from:
		frappe.db.set_value(
			"Contract",
			doc.renewed_from,
			{
				"superseded_by": doc.name,
				"vendor_contract_status": "Superseded",
				"superseded_on": now_datetime(),
			},
		)


@frappe.whitelist()
def renew_contract(contract):
	"""Create a draft renewal of `contract`: a new Contract pre-filled with its terms,
	linked back via `renewed_from`. The predecessor is only marked Superseded once this
	new contract is actually submitted (see handle_submit) - a draft renewal in progress
	doesn't yet change the status of the contract it's replacing."""
	if not set(frappe.get_roles(frappe.session.user)) & RENEWAL_ROLES:
		frappe.throw(_("You do not have access to initiate contract renewals."), frappe.PermissionError)

	source = frappe.get_doc("Contract", contract)

	if source.docstatus != 1:
		frappe.throw(_("Only a submitted contract can be renewed."))
	if source.vendor_contract_status not in RENEWABLE_STATUSES:
		frappe.throw(_("Only a contract that is Expiring Soon or Expired can be renewed."))
	if source.superseded_by:
		frappe.throw(_("This contract has already been renewed: {0}").format(source.superseded_by))

	existing = frappe.db.exists("Contract", {"renewed_from": source.name, "docstatus": ["!=", 2]})
	if existing:
		frappe.throw(_("A renewal for this contract already exists: {0}").format(existing))

	new_start = add_days(getdate(source.end_date), 1) if source.end_date else getdate(today())
	new_end = None
	if source.start_date and source.end_date:
		duration = (getdate(source.end_date) - getdate(source.start_date)).days
		new_end = add_days(new_start, duration)

	new_contract = frappe.copy_doc(source)
	new_contract.renewed_from = source.name
	new_contract.superseded_by = None
	new_contract.start_date = new_start
	new_contract.end_date = new_end
	new_contract.vendor_contract_status = "Draft"
	new_contract.expiry_alert_sent = 0
	new_contract.expiry_escalation_sent = 0
	new_contract.is_signed = 0
	new_contract.signee = None
	new_contract.signed_on = None
	new_contract.signed_by_company = None
	new_contract.amended_from = None
	new_contract.insert(ignore_permissions=True)

	return {"name": new_contract.name}


@frappe.whitelist()
def initiate_amendment(contract, changes, reason):
	"""Submit a Contract Amendment Request for `contract` (an Active contract), routing it to
	whichever approver role the Contract Amendment Settings matrix requires. Nothing changes on
	the Contract itself until the request is approved - see ContractAmendmentRequest.approve()."""
	if isinstance(changes, str):
		changes = frappe.parse_json(changes)

	if not set(frappe.get_roles(frappe.session.user)) & AMENDMENT_ROLES:
		frappe.throw(_("You do not have access to amend contracts."), frappe.PermissionError)
	if not reason:
		frappe.throw(_("A reason is required to amend a contract."))

	source = frappe.get_doc("Contract", contract)
	if source.docstatus != 1 or source.vendor_contract_status != "Active":
		frappe.throw(_("Only an active contract can be amended."))

	rows = _diff_contract_changes(source, changes)
	required_role = _required_approval_role(source, rows)

	request = frappe.get_doc(
		{
			"doctype": "Contract Amendment Request",
			"contract": source.name,
			"vendor": source.vendor,
			"reason": reason,
			"required_approval_role": required_role,
			"changes": rows,
			"requested_by": frappe.session.user,
			"requested_on": now_datetime(),
		}
	)
	request.insert(ignore_permissions=True)

	_notify_amendment_approvers(request)

	return {"name": request.name}


def _diff_contract_changes(contract, changes):
	rows = []
	for fieldname, value in (changes or {}).items():
		if fieldname not in AMENDABLE_FIELDS:
			frappe.throw(_("{0} is not an amendable contract field.").format(fieldname))

		label = AMENDABLE_FIELDS[fieldname]

		if fieldname == "commodity_scope":
			old_value = ", ".join(sorted(row.commodity_category for row in contract.commodity_scope))
			value = ", ".join(sorted({v.strip() for v in (value or "").split(",") if v.strip()}))
		else:
			old_value = contract.get(fieldname)

		if str(old_value or "") == str(value or ""):
			continue

		rows.append({"fieldname": fieldname, "field_label": label, "old_value": old_value, "new_value": value})

	if not rows:
		frappe.throw(_("No changes were submitted."))

	return rows


def _required_approval_role(contract, rows):
	settings = frappe.get_cached_doc("Contract Amendment Settings")
	value_row = next((r for r in rows if r["fieldname"] == "contract_value"), None)

	if value_row:
		old = float(contract.contract_value or 0)
		new = float(value_row["new_value"] or 0)
		pct_change = abs(new - old) / old * 100 if old else 100
		if pct_change > (settings.major_value_change_threshold_percent or 10):
			return settings.senior_approver_role or "Head of Finance/Finance Approver"

	return settings.standard_approver_role or "Purchase Manager"


def _notify_amendment_approvers(request):
	from elmn.api.emails import send_templated_email
	from elmn.api.notification import create_notification_log, users_with_role

	users = users_with_role(request.required_approval_role)
	if not users:
		frappe.logger().warning(
			f"Contract Amendment Request {request.name}: no {request.required_approval_role} to notify"
		)
		return

	vendor_name = request.contract
	if request.vendor:
		vendor_name = frappe.db.get_value("Supplier", request.vendor, "supplier_name") or request.contract

	subject = _("Contract amendment pending your approval: {0}").format(request.contract)

	send_templated_email(
		"contract_amendment_pending",
		[u.email for u in users if u.email],
		{
			"contract_id": request.contract,
			"vendor_name": vendor_name,
			"reason": request.reason,
			"requested_by": request.requested_by,
			"changes": [
				{"label": row.field_label, "old": row.old_value, "new": row.new_value}
				for row in request.changes
			],
			"url": frappe.utils.get_url(f"/app/contract-amendment-request/{request.name}"),
		},
		default_subject=subject,
		reference_doctype=request.doctype,
		reference_name=request.name,
	)
	create_notification_log([u.name for u in users], subject, request)


@frappe.whitelist()
def initiate_termination(contract, termination_reason_category, reason, supporting_document):
	"""Submit a Contract Termination Request for an Active contract, routed to whichever role
	Contract Termination Settings names as the secondary approver. Nothing changes on the
	Contract until the request is approved - see ContractTerminationRequest.approve()."""
	if not set(frappe.get_roles(frappe.session.user)) & TERMINATION_ROLES:
		frappe.throw(_("You do not have access to terminate contracts."), frappe.PermissionError)
	if not termination_reason_category or not reason:
		frappe.throw(_("A termination reason is required."))
	if not supporting_document:
		frappe.throw(_("Supporting documentation is required to terminate a contract."))

	source = frappe.get_doc("Contract", contract)
	if source.docstatus != 1 or source.vendor_contract_status != "Active":
		frappe.throw(_("Only an active contract can be terminated."))

	existing = frappe.db.exists(
		"Contract Termination Request", {"contract": source.name, "status": "Pending Approval"}
	)
	if existing:
		frappe.throw(
			_("A termination request for this contract is already pending: {0}").format(existing)
		)

	settings = frappe.get_cached_doc("Contract Termination Settings")
	required_role = settings.termination_approver_role or "Head of Procurement/ Procurement Approver"

	request = frappe.get_doc(
		{
			"doctype": "Contract Termination Request",
			"contract": source.name,
			"vendor": source.vendor,
			"termination_reason_category": termination_reason_category,
			"reason": reason,
			"supporting_document": supporting_document,
			"required_approval_role": required_role,
			"requested_by": frappe.session.user,
			"requested_on": now_datetime(),
		}
	)
	request.insert(ignore_permissions=True)

	_notify_termination_approvers(request)

	return {"name": request.name}


def _notify_termination_approvers(request):
	from elmn.api.emails import send_templated_email
	from elmn.api.notification import create_notification_log, users_with_role

	users = users_with_role(request.required_approval_role)
	if not users:
		frappe.logger().warning(
			f"Contract Termination Request {request.name}: no {request.required_approval_role} to notify"
		)
		return

	vendor_name = request.contract
	if request.vendor:
		vendor_name = frappe.db.get_value("Supplier", request.vendor, "supplier_name") or request.contract

	subject = _("Contract termination pending your approval: {0}").format(request.contract)

	send_templated_email(
		"contract_termination_pending",
		[u.email for u in users if u.email],
		{
			"contract_id": request.contract,
			"vendor_name": vendor_name,
			"reason_category": request.termination_reason_category,
			"reason": request.reason,
			"requested_by": request.requested_by,
			"url": frappe.utils.get_url(f"/app/contract-termination-request/{request.name}"),
		},
		default_subject=subject,
		reference_doctype=request.doctype,
		reference_name=request.name,
	)
	create_notification_log([u.name for u in users], subject, request)


def _compute_status(doc):
	if doc.vendor_contract_status in TERMINAL_STATUSES:
		return doc.vendor_contract_status
	if doc.docstatus == 0:
		return "Draft"
	return _status_from_dates(doc.end_date)


def _status_from_dates(end_date):
	if not end_date:
		return "Active"

	end = getdate(end_date)
	now = getdate(today())
	lead_days, _escalation_days = _alert_settings()

	if end < now:
		return "Expired"
	if (end - now).days <= lead_days:
		return "Expiring Soon"
	return "Active"


def refresh_contract_statuses():
	"""Daily scheduled job: keep Active/Expiring Soon/Expired in sync with today's date
	for contracts that aren't being actively edited."""
	contracts = frappe.get_all(
		"Contract",
		filters={
			"docstatus": 1,
			"vendor_contract_status": ["not in", list(TERMINAL_STATUSES)],
		},
		fields=["name", "end_date", "vendor_contract_status"],
	)

	for contract in contracts:
		new_status = _status_from_dates(contract.end_date)
		if new_status != contract.vendor_contract_status:
			frappe.db.set_value("Contract", contract.name, "vendor_contract_status", new_status)


def send_contract_expiry_alerts():
	"""Daily scheduled job: alert the assigned Procurement Officer(s) when a vendor
	contract is approaching expiry, with a second escalation alert closer to the date
	if it still hasn't been renewed, terminated, or superseded."""
	lead_days, escalation_days = _alert_settings()
	now = getdate(today())

	contracts = frappe.get_all(
		"Contract",
		filters={
			"docstatus": 1,
			"vendor_contract_status": "Expiring Soon",
			"end_date": ["is", "set"],
		},
		fields=["name", "end_date", "expiry_alert_sent", "expiry_escalation_sent"],
	)

	for contract in contracts:
		days_left = (getdate(contract.end_date) - now).days

		if not contract.expiry_alert_sent and days_left <= lead_days:
			_send_expiry_alert(contract.name, escalation=False)
			frappe.db.set_value("Contract", contract.name, "expiry_alert_sent", 1)

		if not contract.expiry_escalation_sent and days_left <= escalation_days:
			_send_expiry_alert(contract.name, escalation=True)
			frappe.db.set_value("Contract", contract.name, "expiry_escalation_sent", 1)


def _assigned_users(contract_name):
	assigned = frappe.get_all(
		"ToDo",
		filters={"reference_type": "Contract", "reference_name": contract_name, "status": "Open"},
		pluck="allocated_to",
	)
	if assigned:
		return sorted(set(assigned))

	from elmn.api.notification import users_with_role

	return [u.name for u in users_with_role("Procurement Officer")]


def _send_expiry_alert(contract_name, escalation):
	from elmn.api.emails import send_templated_email
	from elmn.api.notification import create_notification_log

	contract = frappe.get_doc("Contract", contract_name)
	recipients = _assigned_users(contract_name)
	if not recipients:
		frappe.logger().warning(f"Contract {contract_name}: no Procurement Officer to alert")
		return

	vendor_name = contract.vendor
	if contract.vendor:
		vendor_name = frappe.db.get_value("Supplier", contract.vendor, "supplier_name") or contract.vendor
	elif contract.party_name:
		vendor_name = contract.party_name

	commodity_scope = ", ".join(row.commodity_category for row in contract.commodity_scope) or "-"
	days_left = (getdate(contract.end_date) - getdate(today())).days

	recommended_action = (
		_(
			"No renewal action has been recorded yet. Confirm renewal, initiate a new agreement, "
			"or record termination before this contract expires."
		)
		if escalation
		else _(
			"Initiate renewal discussions with the vendor, or confirm the contract will be "
			"terminated or superseded, before it expires."
		)
	)

	template = "contract_expiry_escalation" if escalation else "contract_expiry_alert"
	subject = (
		_("URGENT: Vendor contract expiring in {0} day(s): {1}").format(days_left, contract_name)
		if escalation
		else _("Vendor contract expiring soon: {0}").format(contract_name)
	)

	user_emails = frappe.get_all(
		"User", filters={"name": ["in", recipients], "enabled": 1}, pluck="email"
	)

	send_templated_email(
		template,
		[e for e in user_emails if e],
		{
			"contract_id": contract.name,
			"vendor_name": vendor_name,
			"expiry_date": formatdate(contract.end_date),
			"days_left": days_left,
			"commodity_scope": commodity_scope,
			"recommended_action": recommended_action,
			"url": frappe.utils.get_url(f"/app/contract/{contract.name}"),
		},
		default_subject=subject,
		reference_doctype=contract.doctype,
		reference_name=contract.name,
	)
	create_notification_log(recipients, subject, contract)


VERSION_COMPARE_FIELDS = {
	"contract_type": "Contract type",
	"start_date": "Start date",
	"end_date": "End date",
	"contract_value": "Contract value",
	"contract_terms": "Contract terms",
}


@frappe.whitelist()
def get_contract_version_history(contract):
	"""Walk a contract's renewed_from/superseded_by chain (built by renewal, VPM.030, and
	amendment, VPM.031) end to end and return every version in chronological order, with the
	changed terms and approver for each step - amendment steps use the authoritative
	Contract Amendment Item snapshot; renewal steps are diffed live since renewal doesn't keep
	a separate change log."""
	current = frappe.get_doc("Contract", contract)

	visited = set()
	while current.renewed_from and current.renewed_from not in visited:
		visited.add(current.name)
		current = frappe.get_doc("Contract", current.renewed_from)
	root = current

	chain = []
	node = root
	seen = set()
	while node and node.name not in seen:
		seen.add(node.name)
		chain.append(node)
		node = frappe.get_doc("Contract", node.superseded_by) if node.superseded_by else None

	versions = []
	for idx, version in enumerate(chain, start=1):
		predecessor = chain[idx - 2] if idx > 1 else None
		changes = []
		change_type = "Original"
		reason = None

		if predecessor:
			amendment = frappe.db.get_value(
				"Contract Amendment Request",
				{"contract": predecessor.name, "new_contract": version.name, "status": "Approved"},
				["name", "reason"],
				as_dict=True,
			)
			if amendment:
				change_type = "Amendment"
				reason = amendment.reason
				changes = frappe.get_all(
					"Contract Amendment Item",
					filters={"parent": amendment.name},
					fields=["field_label", "old_value", "new_value"],
				)
			else:
				change_type = "Renewal"
				changes = _diff_contract_versions(predecessor, version)

		versions.append(
			{
				"version": idx,
				"contract": version.name,
				"change_type": change_type,
				"status": version.vendor_contract_status,
				"start_date": version.start_date,
				"end_date": version.end_date,
				"contract_value": version.contract_value,
				"activated_on": version.activated_on,
				"activated_by": version.activated_by,
				"superseded_on": version.superseded_on,
				"reason": reason,
				"changes": changes,
			}
		)

	return versions


def _diff_contract_versions(old, new):
	rows = []
	for fieldname, label in VERSION_COMPARE_FIELDS.items():
		old_value = old.get(fieldname)
		new_value = new.get(fieldname)
		if str(old_value or "") != str(new_value or ""):
			rows.append({"field_label": label, "old_value": old_value, "new_value": new_value})

	old_scope = ", ".join(sorted(row.commodity_category for row in old.commodity_scope))
	new_scope = ", ".join(sorted(row.commodity_category for row in new.commodity_scope))
	if old_scope != new_scope:
		rows.append({"field_label": "Commodity scope", "old_value": old_scope, "new_value": new_scope})

	return rows
