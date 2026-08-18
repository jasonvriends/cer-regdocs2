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

output "log_analytics_workspace_id" {
  description = "Workspace customer ID used by the protected Atlas error lookup API."
  value       = azurerm_log_analytics_workspace.main.workspace_id
}

output "diagnostics_operator_token" {
  description = "Bearer token for /api/diagnostics/errors and protected live diagnostics. Retrieve explicitly with terraform output -raw diagnostics_operator_token."
  value       = random_password.diagnostics_operator_token.result
  sensitive   = true
}

output "index_job_name" {
  value = local.index_job_name
}

output "intelligence_job_name" {
  value = local.intelligence_job_name
}

output "deployed_ui_image_tag" {
  description = "Last UI image tag applied to the Container App."
  value       = var.deploy_workloads ? var.ui_image_tag : null
}

output "deployed_indexer_image_tag" {
  description = "Last shared publisher image tag applied to the index and intelligence jobs."
  value       = var.deploy_workloads ? var.indexer_image_tag : null
}

output "ui_url" {
  value = var.deploy_workloads ? "https://${azurerm_container_app.ui[0].latest_revision_fqdn}" : null
}

output "source_storage_account_id" {
  description = "Constructed resource ID; the existing Storage account is outside Terraform ownership."
  value       = local.source_storage_id
}
