import frappe
from frappe import _
from frappe.utils import format_date, get_url

from elmn.api.emails import send_templated_email
from elmn.api.notification import create_notification_log, users_with_role


def _draft_resume_url(vendor_application):
	return get_url(f"/vendor-registration?draft_token={vendor_application.draft_token}")


def notify_draft_saved(vendor_application):
	if not vendor_application.primary_contact_email:
		return

	settings = frappe.get_single("Invitation Settings")

	send_templated_email(
		"vendor_draft_saved",
		[vendor_application.primary_contact_email],
		{
			"primary_contact_name": vendor_application.primary_contact_name,
			"legal_entity_name": vendor_application.legal_entity_name,
			"resume_url": _draft_resume_url(vendor_application),
			"draft_expiry_days": settings.draft_expiry_days,
		},
		default_subject=_("Continue your vendor application: {0}").format(vendor_application.legal_entity_name),
		reference_doctype=vendor_application.doctype,
		reference_name=vendor_application.name,
	)


def notify_draft_expiry_warning(vendor_application):
	if not vendor_application.primary_contact_email:
		return

	send_templated_email(
		"vendor_draft_expiry_warning",
		[vendor_application.primary_contact_email],
		{
			"primary_contact_name": vendor_application.primary_contact_name,
			"legal_entity_name": vendor_application.legal_entity_name,
			"resume_url": _draft_resume_url(vendor_application),
			"expires_on": format_date(vendor_application.draft_expires_on),
		},
		default_subject=_("Your vendor application draft is expiring soon: {0}").format(
			vendor_application.legal_entity_name
		),
		reference_doctype=vendor_application.doctype,
		reference_name=vendor_application.name,
	)


def notify_applicant_submission_received(vendor_application):
	if not vendor_application.primary_contact_email:
		return

	settings = frappe.get_single("Invitation Settings")
	document_names = [row.document_type for row in vendor_application.documents if row.attachment]
	support_contact = " / ".join(filter(None, [settings.support_email, settings.support_phone]))

	send_templated_email(
		"vendor_submission_confirmation",
		[vendor_application.primary_contact_email],
		{
			"primary_contact_name": vendor_application.primary_contact_name,
			"legal_entity_name": vendor_application.legal_entity_name,
			"reference_number": vendor_application.name,
			"submission_date": format_date(vendor_application.creation),
			"document_names": document_names,
			"expected_processing_days": settings.expected_processing_days,
			"support_contact": support_contact,
		},
		default_subject=_("We've received your vendor application: {0}").format(vendor_application.name),
		reference_doctype=vendor_application.doctype,
		reference_name=vendor_application.name,
	)


def notify_admin_of_new_submission(vendor_application):
	users = users_with_role("Procurement Officer") or users_with_role("System Manager")
	if not users:
		frappe.logger().warning(
			f"Vendor Application {vendor_application.name}: no Procurement Officer (or System Manager) to notify"
		)
		return

	recipients = [u.email for u in users if u.email]
	subject = _("New vendor application submitted: {0}").format(vendor_application.legal_entity_name)
	review_url = get_url(f"/app/vendor-application/{vendor_application.name}")

	send_templated_email(
		"vendor_submission_pending_review",
		recipients,
		{
			"reference_number": vendor_application.name,
			"legal_entity_name": vendor_application.legal_entity_name,
			"vendor_type": vendor_application.vendor_type,
			"submission_date": format_date(vendor_application.creation),
			"review_url": review_url,
		},
		default_subject=subject,
		reference_doctype=vendor_application.doctype,
		reference_name=vendor_application.name,
	)
	create_notification_log([u.name for u in users], subject, vendor_application)


def _rfi_response_url(rfi):
	return get_url(f"/vendor-rfi-response?token={rfi.response_token}")


def _notify_email_and_inapp(email, user_names_for_log, template, args, default_subject, doc):
	if email:
		send_templated_email(
			template,
			[email],
			args,
			default_subject=default_subject,
			reference_doctype=doc.doctype,
			reference_name=doc.name,
		)
	if user_names_for_log:
		create_notification_log(user_names_for_log, default_subject, doc)


def notify_rfi_raised(rfi):
	vendor_application = frappe.get_doc("Vendor Application", rfi.vendor_application)
	subject = _("MLN needs more information for your vendor application: {0}").format(
		vendor_application.legal_entity_name
	)


	matching_user = frappe.db.get_value(
		"User", {"email": vendor_application.primary_contact_email, "enabled": 1}, "name"
	)

	_notify_email_and_inapp(
		vendor_application.primary_contact_email,
		[matching_user] if matching_user else None,
		"vendor_rfi_raised",
		{
			"primary_contact_name": vendor_application.primary_contact_name,
			"legal_entity_name": vendor_application.legal_entity_name,
			"reference_number": vendor_application.name,
			"explanation": rfi.explanation,
			"items": [row.description for row in rfi.items],
			"response_deadline": format_date(rfi.response_deadline),
			"response_url": _rfi_response_url(rfi),
		},
		subject,
		rfi,
	)


def notify_rfi_responded(rfi):
	if not rfi.raised_by:
		return

	subject = _("Vendor responded to your Request for Information: {0}").format(rfi.vendor_application)
	recipient_email = frappe.db.get_value("User", rfi.raised_by, "email")
	review_url = get_url(f"/app/vendor-rfi/{rfi.name}")

	send_templated_email(
		"vendor_rfi_responded",
		[recipient_email] if recipient_email else [],
		{
			"reference_number": rfi.vendor_application,
			"rfi_name": rfi.name,
			"review_url": review_url,
		},
		default_subject=subject,
		reference_doctype=rfi.doctype,
		reference_name=rfi.name,
	)
	create_notification_log([rfi.raised_by], subject, rfi)


def notify_approval(vendor_application, user_doc):
	if not vendor_application.primary_contact_email:
		return

	user_doc.validate_reset_password()
	link = user_doc._reset_password(send_email=False)

	send_templated_email(
		"vendor_approved",
		[vendor_application.primary_contact_email],
		{
			"primary_contact_name": vendor_application.primary_contact_name,
			"legal_entity_name": vendor_application.legal_entity_name,
			"vendor_id": vendor_application.supplier,
			"link": link,
		},
		default_subject=_("Your vendor application has been approved: {0}").format(
			vendor_application.legal_entity_name
		),
		reference_doctype=vendor_application.doctype,
		reference_name=vendor_application.name,
	)
