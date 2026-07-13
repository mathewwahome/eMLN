import frappe


def clear_workspaces():
    try:
        frappe.logger().info("Starting the process to delete unnecessary workspaces.")

        # Get the list of workspaces to delete
        workspaces_to_delete = frappe.get_list("Workspace", filters={"module": ["!=", "eLMN"]})

        frappe.logger().info(f"Workspaces to delete: {workspaces_to_delete}")

        # Iterate through the list and delete each workspace
        for workspace in workspaces_to_delete:
            frappe.delete_doc("Workspace", workspace['name'], force=True)
            frappe.logger().info(f"Deleted workspace: {workspace['name']}")

        # Commit the transaction to apply the changes
        frappe.db.commit()
        frappe.logger().info("Deletion process completed successfully.")

    except Exception as e:
        frappe.logger().error(f"An error occurred while deleting workspaces: {e}")
        frappe.db.rollback()  # Rollback in case of error


def post_install():
    clear_workspaces()
