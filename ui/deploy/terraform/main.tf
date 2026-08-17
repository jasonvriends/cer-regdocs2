locals {
  registry_name          = "crregdocs${var.name_suffix}"
  search_name            = "srch-regdocs-${var.name_suffix}"
  foundry_name           = "aif-regdocs-${var.name_suffix}"
  foundry_project_name   = "regdocs-atlas"
  app_environment_name   = "cae-regdocs-${var.name_suffix}"
  ui_app_name            = "app-regdocs-${var.name_suffix}"
  index_job_name         = "job-regdocs-${var.name_suffix}"
  ui_identity_name       = "id-regdocs-ui-${var.name_suffix}"
  index_identity_name    = "id-regdocs-index-${var.name_suffix}"
  foundry_user_role_id   = "/subscriptions/${var.subscription_id}/providers/Microsoft.Authorization/roleDefinitions/53ca6127-db72-4b80-b1b0-d745d6d5456d"
  foundry_project_url    = "https://${local.foundry_name}.services.ai.azure.com/api/projects/${local.foundry_project_name}"
  azure_openai_endpoint  = "https://${local.foundry_name}.openai.azure.com"
  normalized_blob_prefix = trimsuffix(var.normalized_blob_prefix, "/")
  source_storage_id      = "/subscriptions/${var.storage_subscription_id}/resourceGroups/${var.storage_resource_group_name}/providers/Microsoft.Storage/storageAccounts/${var.storage_account_name}"
}

resource "azurerm_resource_group" "main" {
  name     = var.resource_group_name
  location = var.location
  tags     = var.tags
}

resource "random_password" "foundry_safety_salt" {
  length  = 48
  special = false
}

resource "azurerm_container_registry" "main" {
  name                = local.registry_name
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location
  sku                 = "Basic"
  admin_enabled       = false
  tags                = var.tags
}

resource "azurerm_log_analytics_workspace" "main" {
  name                = "log-regdocs-${var.name_suffix}"
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location
  sku                 = "PerGB2018"
  retention_in_days   = 30
  tags                = var.tags
}

resource "azurerm_container_app_environment" "main" {
  name                       = local.app_environment_name
  resource_group_name        = azurerm_resource_group.main.name
  location                   = azurerm_resource_group.main.location
  log_analytics_workspace_id = azurerm_log_analytics_workspace.main.id
  tags                       = var.tags
}

resource "azurerm_user_assigned_identity" "ui" {
  name                = local.ui_identity_name
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location
  tags                = var.tags
}

resource "azurerm_user_assigned_identity" "indexer" {
  name                = local.index_identity_name
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location
  tags                = var.tags
}

resource "azurerm_search_service" "main" {
  name                          = local.search_name
  resource_group_name           = azurerm_resource_group.main.name
  location                      = var.search_location
  sku                           = var.search_sku
  partition_count               = var.search_partition_count
  replica_count                 = var.search_replica_count
  semantic_search_sku           = var.search_semantic_sku
  local_authentication_enabled  = false
  public_network_access_enabled = true

  identity {
    type = "SystemAssigned"
  }

  tags = var.tags
}

resource "azurerm_cognitive_account" "foundry" {
  name                       = local.foundry_name
  resource_group_name        = azurerm_resource_group.main.name
  location                   = var.foundry_location
  kind                       = "AIServices"
  sku_name                   = "S0"
  custom_subdomain_name      = local.foundry_name
  project_management_enabled = true
  local_auth_enabled         = false

  identity {
    type = "SystemAssigned"
  }

  tags = var.tags
}

resource "azurerm_cognitive_account_project" "main" {
  name                 = local.foundry_project_name
  cognitive_account_id = azurerm_cognitive_account.foundry.id
  location             = azurerm_cognitive_account.foundry.location
  display_name         = "REGDOCS Atlas"
  description          = "Evidence-first research for public Canada Energy Regulator records"

  identity {
    type = "SystemAssigned"
  }

  tags = var.tags
}

resource "azurerm_cognitive_deployment" "embedding" {
  name                 = var.embedding_deployment_name
  cognitive_account_id = azurerm_cognitive_account.foundry.id

  model {
    format  = "OpenAI"
    name    = var.embedding_model_name
    version = var.embedding_model_version
  }

  sku {
    name     = var.embedding_sku
    capacity = var.embedding_capacity
  }

  version_upgrade_option = "NoAutoUpgrade"
}

resource "azurerm_cognitive_deployment" "chat" {
  name                 = var.chat_deployment_name
  cognitive_account_id = azurerm_cognitive_account.foundry.id

  model {
    format  = "OpenAI"
    name    = var.chat_model_name
    version = var.chat_model_version
  }

  sku {
    name     = var.chat_sku
    capacity = var.chat_capacity
  }

  version_upgrade_option = "NoAutoUpgrade"
}

resource "azurerm_role_assignment" "ui_acr_pull" {
  scope                            = azurerm_container_registry.main.id
  role_definition_name             = "AcrPull"
  principal_id                     = azurerm_user_assigned_identity.ui.principal_id
  skip_service_principal_aad_check = true
}

resource "azurerm_role_assignment" "indexer_acr_pull" {
  scope                            = azurerm_container_registry.main.id
  role_definition_name             = "AcrPull"
  principal_id                     = azurerm_user_assigned_identity.indexer.principal_id
  skip_service_principal_aad_check = true
}

resource "azurerm_role_assignment" "ui_search_reader" {
  scope                            = azurerm_search_service.main.id
  role_definition_name             = "Search Index Data Reader"
  principal_id                     = azurerm_user_assigned_identity.ui.principal_id
  skip_service_principal_aad_check = true
}

resource "azurerm_role_assignment" "indexer_search_service" {
  scope                            = azurerm_search_service.main.id
  role_definition_name             = "Search Service Contributor"
  principal_id                     = azurerm_user_assigned_identity.indexer.principal_id
  skip_service_principal_aad_check = true
}

resource "azurerm_role_assignment" "indexer_search_data" {
  scope                            = azurerm_search_service.main.id
  role_definition_name             = "Search Index Data Contributor"
  principal_id                     = azurerm_user_assigned_identity.indexer.principal_id
  skip_service_principal_aad_check = true
}

resource "azurerm_role_assignment" "indexer_storage" {
  scope                            = local.source_storage_id
  role_definition_name             = "Storage Blob Data Contributor"
  principal_id                     = azurerm_user_assigned_identity.indexer.principal_id
  skip_service_principal_aad_check = true
}

resource "azurerm_role_assignment" "indexer_openai" {
  scope                            = azurerm_cognitive_account.foundry.id
  role_definition_name             = "Cognitive Services OpenAI User"
  principal_id                     = azurerm_user_assigned_identity.indexer.principal_id
  skip_service_principal_aad_check = true
}

resource "azurerm_role_assignment" "search_openai" {
  scope                            = azurerm_cognitive_account.foundry.id
  role_definition_name             = "Cognitive Services OpenAI User"
  principal_id                     = azurerm_search_service.main.identity[0].principal_id
  skip_service_principal_aad_check = true
}

resource "azurerm_role_assignment" "ui_openai" {
  scope                            = azurerm_cognitive_account.foundry.id
  role_definition_name             = "Cognitive Services OpenAI User"
  principal_id                     = azurerm_user_assigned_identity.ui.principal_id
  skip_service_principal_aad_check = true
}

resource "azurerm_role_assignment" "ui_foundry_user" {
  scope                            = azurerm_cognitive_account_project.main.id
  role_definition_id               = local.foundry_user_role_id
  principal_id                     = azurerm_user_assigned_identity.ui.principal_id
  skip_service_principal_aad_check = true
}

resource "azurerm_role_assignment" "project_foundry_user" {
  scope                            = azurerm_cognitive_account.foundry.id
  role_definition_id               = local.foundry_user_role_id
  principal_id                     = azurerm_cognitive_account_project.main.identity[0].principal_id
  skip_service_principal_aad_check = true
}

resource "azurerm_container_app" "ui" {
  count                        = var.deploy_workloads ? 1 : 0
  name                         = local.ui_app_name
  container_app_environment_id = azurerm_container_app_environment.main.id
  resource_group_name          = azurerm_resource_group.main.name
  revision_mode                = "Single"

  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.ui.id]
  }

  registry {
    server   = azurerm_container_registry.main.login_server
    identity = azurerm_user_assigned_identity.ui.id
  }

  ingress {
    external_enabled           = true
    allow_insecure_connections = false
    target_port                = 3000
    transport                  = "auto"

    dynamic "ip_security_restriction" {
      for_each = {
        for index, cidr in var.ui_allowed_ip_cidrs : format("allow-%02d", index + 1) => cidr
      }

      content {
        name             = ip_security_restriction.key
        description      = "Allowed by the Terraform UI ingress allowlist"
        ip_address_range = ip_security_restriction.value
        action           = "Allow"
      }
    }

    traffic_weight {
      latest_revision = true
      percentage      = 100
    }
  }

  template {
    min_replicas = 0
    max_replicas = 2

    http_scale_rule {
      name                = "http"
      concurrent_requests = 50
    }

    container {
      name   = "regdocs-ui"
      image  = "${azurerm_container_registry.main.login_server}/regdocs-ui:${var.image_tag}"
      cpu    = 0.5
      memory = "1Gi"

      env {
        name  = "AZURE_CLIENT_ID"
        value = azurerm_user_assigned_identity.ui.client_id
      }
      env {
        name  = "AZURE_SEARCH_ENDPOINT"
        value = azurerm_search_service.main.endpoint
      }
      env {
        name  = "AZURE_SEARCH_INDEX"
        value = var.search_index_name
      }
      env {
        name  = "AZURE_SEARCH_VECTOR_FIELD"
        value = var.search_vector_field
      }
      env {
        name  = "AZURE_SEARCH_SEMANTIC_CONFIGURATION"
        value = var.search_semantic_configuration
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
        name  = "FOUNDRY_SAFETY_SALT"
        value = random_password.foundry_safety_salt.result
      }
    }
  }

  depends_on = [
    azurerm_role_assignment.ui_acr_pull,
    azurerm_role_assignment.ui_foundry_user,
    azurerm_role_assignment.ui_openai,
    azurerm_role_assignment.ui_search_reader,
  ]

  tags = var.tags
}

resource "azurerm_container_app_job" "indexer" {
  count                        = var.deploy_workloads ? 1 : 0
  name                         = local.index_job_name
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
      name   = "regdocs-indexer"
      image  = "${azurerm_container_registry.main.login_server}/regdocs-indexer:${var.image_tag}"
      cpu    = 2
      memory = "4Gi"

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
        name  = "REGDOCS_EMBEDDING_CACHE_BLOB"
        value = var.embedding_cache_blob
      }
      env {
        name  = "AZURE_SEARCH_ENDPOINT"
        value = azurerm_search_service.main.endpoint
      }
      env {
        name  = "AZURE_SEARCH_HYBRID_INDEX"
        value = var.search_index_name
      }
      env {
        name  = "AZURE_SEARCH_VECTOR_FIELD"
        value = var.search_vector_field
      }
      env {
        name  = "AZURE_OPENAI_ENDPOINT"
        value = local.azure_openai_endpoint
      }
      env {
        name  = "AZURE_OPENAI_EMBEDDING_DEPLOYMENT"
        value = var.embedding_deployment_name
      }
      env {
        name  = "AZURE_OPENAI_EMBEDDING_MODEL"
        value = var.embedding_model_name
      }
      env {
        name  = "AZURE_OPENAI_EMBEDDING_DIMENSIONS"
        value = tostring(var.embedding_dimensions)
      }
      env {
        name  = "REGDOCS_EMBEDDING_BATCH_SIZE"
        value = tostring(var.embedding_batch_size)
      }
      env {
        name  = "REGDOCS_SEARCH_UPLOAD_BATCH_SIZE"
        value = tostring(var.search_upload_batch_size)
      }
      env {
        name  = "PYTHONUNBUFFERED"
        value = "1"
      }
    }
  }

  depends_on = [
    azurerm_cognitive_deployment.embedding,
    azurerm_role_assignment.indexer_acr_pull,
    azurerm_role_assignment.indexer_openai,
    azurerm_role_assignment.indexer_search_data,
    azurerm_role_assignment.indexer_search_service,
    azurerm_role_assignment.indexer_storage,
    azurerm_role_assignment.search_openai,
  ]

  tags = var.tags
}
