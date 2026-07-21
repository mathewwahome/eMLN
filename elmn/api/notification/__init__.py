import frappe


def users_with_role(role):
	user_names = frappe.get_all(
		"Has Role", filters={"role": role, "parenttype": "User"}, pluck="parent"
	)
	return frappe.get_all(
		"User", filters={"name": ["in", user_names], "enabled": 1}, fields=["name", "email"]
	)


def create_notification_log(user_names, subject, doc):
	for user in user_names:
		frappe.get_doc(
			{
				"doctype": "Notification Log",
				"for_user": user,
				"subject": subject,
				"type": "Alert",
				"document_type": doc.doctype,
				"document_name": doc.name,
			}
		).insert(ignore_permissions=True)
