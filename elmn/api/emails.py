import frappe


def send_templated_email(
	template_name, recipients, args, default_subject, reference_doctype=None, reference_name=None
):
	if frappe.db.exists("Email Template", template_name):
		email_template = frappe.get_doc("Email Template", template_name)
		frappe.sendmail(
			recipients=recipients,
			subject=email_template.get_formatted_subject(args),
			message=email_template.get_formatted_response(args),
			reference_doctype=reference_doctype,
			reference_name=reference_name,
		)
	else:
		frappe.sendmail(
			recipients=recipients,
			subject=default_subject,
			template=template_name,
			args=args,
			reference_doctype=reference_doctype,
			reference_name=reference_name,
		)
