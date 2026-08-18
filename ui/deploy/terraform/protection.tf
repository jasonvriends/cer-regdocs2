# These resources use globally significant names and are expensive or disruptive
# to recreate. Foundry resources are also soft-deleted by Azure, which can block
# immediate reuse of the same name. Keep these locks in place during normal
# updates. Intentional teardown requires a deliberate code change that removes
# the prevent_destroy lifecycle before the locks can be deleted.

resource "azurerm_management_lock" "acr_delete_protection" {
  name       = "regdocs-acr-delete-protection"
  scope      = azurerm_container_registry.main.id
  lock_level = "CanNotDelete"
  notes      = "Protect REGDOCS Atlas ACR from accidental deletion."

  lifecycle {
    prevent_destroy = true
  }
}

resource "azurerm_management_lock" "search_delete_protection" {
  name       = "regdocs-search-delete-protection"
  scope      = azurerm_search_service.main.id
  lock_level = "CanNotDelete"
  notes      = "Protect REGDOCS Atlas Azure AI Search from accidental deletion."

  lifecycle {
    prevent_destroy = true
  }
}

resource "azurerm_management_lock" "foundry_delete_protection" {
  name       = "regdocs-foundry-delete-protection"
  scope      = azurerm_cognitive_account.foundry.id
  lock_level = "CanNotDelete"
  notes      = "Protect REGDOCS Atlas Microsoft Foundry from accidental deletion and soft-delete name reuse delays."

  lifecycle {
    prevent_destroy = true
  }
}
