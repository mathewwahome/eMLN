import frappe


def get_context(context):
	context.title = "MediLink — Electronic MediLink Network (eMLN)"
	context.description = (
		"eMLN connects private hospitals and verified pharmaceutical suppliers "
		"through MediLink's coordinated procurement platform."
	)
	context.current_year = frappe.utils.now_datetime().year
	context.full_width = 1
	context.brand_image = (
		frappe.db.get_single_value("Website Settings", "banner_image")
	)
	return context
