output "resource_group_name" {
  value = azurerm_resource_group.main.name
}

output "container_registry_name" {
  value = azurerm_container_registry.main.name
}

output "container_registry_login_server" {
  value = azurerm_container_registry.main.login_server
}

output "search_service_name" {
  value = azurerm_search_service.main.name
}

output "search_endpoint" {
  value = azurerm_search_service.main.endpoint
}

output "foundry_account_name" {
  value = azurerm_cognitive_account.foundry.name
}

output "foundry_project_endpoint" {
  value = local.foundry_project_url
}

output "index_job_name" {
  value = local.index_job_name
}

output "deployed_image_tag" {
  description = "Last image tag applied to the Container App and job."
  value       = var.deploy_workloads ? var.image_tag : null
}

output "ui_url" {
  value = var.deploy_workloads ? "https://${azurerm_container_app.ui[0].latest_revision_fqdn}" : null
}

output "source_storage_account_id" {
  description = "Constructed resource ID; the existing Storage account is outside Terraform ownership."
  value       = local.source_storage_id
}
