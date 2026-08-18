locals {
  intelligence_job_name = "job-regdocs-intelligence-${var.name_suffix}"
}

resource "azurerm_role_assignment" "indexer_foundry_user" {
  scope                            = azurerm_cognitive_account_project.main.id
  role_definition_id               = local.foundry_user_role_id
  principal_id                     = azurerm_user_assigned_identity.indexer.principal_id
  skip_service_principal_aad_check = true
}

resource "azurerm_container_app_job" "intelligence" {
  count                        = var.deploy_workloads ? 1 : 0
  name                         = local.intelligence_job_name
  location                     = azurerm_resource_group.main.location
  resource_group_name          = azurerm_resource_group.main.name
  container_app_environment_id = azurerm_container_app_environment.main.id
  replica_timeout_in_seconds   = 43200
  replica_retry_limit          = 1

  manual_trigger_config {
    parallelism              = 1
    replica_completion_count = 1
  }

  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.indexer.id]
  }

  registry {
    server   = azurerm_container_registry.main.login_server
    identity = azurerm_user_assigned_identity.indexer.id
  }

  template {
    container {
      name    = "regdocs-intelligence"
      image   = "${azurerm_container_registry.main.login_server}/regdocs-indexer:${var.indexer_image_tag}"
      command = ["python", "tools/run_cloud_intelligence.py"]
      cpu     = 2
      memory  = "4Gi"

      env {
        name  = "AZURE_CLIENT_ID"
        value = azurerm_user_assigned_identity.indexer.client_id
      }
      env {
        name  = "REGDOCS_STORAGE_ACCOUNT"
        value = var.storage_account_name
      }
      env {
        name  = "REGDOCS_BLOB_CONTAINER"
        value = var.blob_container_name
      }
      env {
        name  = "REGDOCS_NORMALIZED_BLOB_PREFIX"
        value = local.normalized_blob_prefix
      }
      env {
        name  = "REGDOCS_ENRICH_BLOB_PREFIX"
        value = "workspace/6_enrich"
      }
      env {
        name  = "REGDOCS_INTELLIGENCE_CACHE_BLOB"
        value = "workspace/6_enrich/model/extraction.sqlite"
      }
      env {
        name  = "REGDOCS_INTELLIGENCE_CACHE_SYNC_SECONDS"
        value = "900"
      }
      env {
        name  = "FOUNDRY_PROJECT_ENDPOINT"
        value = local.foundry_project_url
      }
      env {
        name  = "FOUNDRY_MODEL_DEPLOYMENT"
        value = var.chat_deployment_name
      }
      env {
        name  = "AZURE_SEARCH_ENDPOINT"
        value = azurerm_search_service.main.endpoint
      }
      env {
        name  = "AZURE_SEARCH_CLAIMS_INDEX"
        value = "regdocs-claims"
      }
      env {
        name  = "AZURE_SEARCH_OBLIGATIONS_INDEX"
        value = "regdocs-obligations"
      }
      env {
        name  = "PYTHONUNBUFFERED"
        value = "1"
      }
    }
  }

  depends_on = [
    azurerm_cognitive_deployment.chat,
    azurerm_role_assignment.indexer_acr_pull,
    azurerm_role_assignment.indexer_foundry_user,
    azurerm_role_assignment.indexer_openai,
    azurerm_role_assignment.indexer_search_data,
    azurerm_role_assignment.indexer_search_service,
    azurerm_role_assignment.indexer_storage,
  ]

  tags = var.tags
}
