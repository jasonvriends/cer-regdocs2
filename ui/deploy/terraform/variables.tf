variable "subscription_id" {
  description = "Azure subscription that receives the new deployment resources."
  type        = string
}

variable "resource_group_name" {
  description = "Resource group created for REGDOCS Atlas."
  type        = string
}

variable "location" {
  description = "Region for Container Apps, ACR, logs, and the resource group."
  type        = string
  default     = "eastus2"
}

variable "search_location" {
  description = "Region for Azure AI Search."
  type        = string
  default     = "eastus2"
}

variable "foundry_location" {
  description = "Region for Microsoft Foundry and its model deployments."
  type        = string
  default     = "eastus2"
}

variable "name_suffix" {
  description = "Stable globally unique lowercase letters/digits suffix used in public Azure resource names. Reuse it with the same remote Terraform state."
  type        = string

  validation {
    condition     = can(regex("^[a-z0-9]{3,12}$", var.name_suffix))
    error_message = "name_suffix must be 3-12 lowercase letters or digits."
  }
}

variable "storage_account_name" {
  description = "Existing Storage account that contains the database and workspace. Terraform never manages it."
  type        = string
}

variable "storage_subscription_id" {
  description = "Subscription containing the existing Storage account."
  type        = string
}

variable "storage_resource_group_name" {
  description = "Resource group containing the existing Storage account."
  type        = string
}

variable "blob_container_name" {
  description = "Existing Blob container containing the normalized workspace."
  type        = string
}

variable "normalized_blob_prefix" {
  type    = string
  default = "workspace/4_normalize"
}

variable "embedding_cache_blob" {
  type    = string
  default = "workspace/5_index/embedding-cache.sqlite"
}

variable "search_sku" {
  type    = string
  default = "standard"
}

variable "search_partition_count" {
  type    = number
  default = 1
}

variable "search_replica_count" {
  type    = number
  default = 1
}

variable "search_semantic_sku" {
  type    = string
  default = "free"
}

variable "search_index_name" {
  type    = string
  default = "regdocs-chunks-hybrid"
}

variable "search_vector_field" {
  type    = string
  default = "content_vector"
}

variable "search_semantic_configuration" {
  type    = string
  default = "regdocs-semantic"
}

variable "ui_allowed_ip_cidrs" {
  description = "IPv4 CIDR ranges allowed to reach the UI and all of its /api routes. An empty list leaves ingress public."
  type        = list(string)
  default     = []

  validation {
    condition     = alltrue([for cidr in var.ui_allowed_ip_cidrs : can(cidrnetmask(cidr))])
    error_message = "Every ui_allowed_ip_cidrs entry must be an IPv4 CIDR, such as 203.0.113.10/32."
  }
}

variable "embedding_deployment_name" {
  type    = string
  default = "regdocs-embedding-3-small"
}

variable "embedding_model_name" {
  type    = string
  default = "text-embedding-3-small"
}

variable "embedding_model_version" {
  type    = string
  default = "1"
}

variable "embedding_sku" {
  description = "GlobalStandard provides broad quota availability for this public corpus."
  type        = string
  default     = "GlobalStandard"
}

variable "embedding_capacity" {
  description = "Thousands of tokens per minute; 1000 requests a 1,000,000 TPM deployment."
  type        = number
  default     = 1000
}

variable "embedding_dimensions" {
  type    = number
  default = 1536
}

variable "embedding_batch_size" {
  description = "Embedding request batch size. 32 is the production-safe default after larger batches caused indexing failures."
  type        = number
  default     = 32
}

variable "search_upload_batch_size" {
  type    = number
  default = 1000
}

variable "chat_deployment_name" {
  type    = string
  default = "regdocs-chat"
}

variable "chat_model_name" {
  description = "Foundry deployment used by grounded Ask and Stage 6 structured extraction."
  type        = string
  default     = "gpt-5.4-mini"

  validation {
    condition     = var.chat_model_name != "gpt-4.1-mini"
    error_message = "gpt-4.1-mini is deprecated for REGDOCS Atlas v1. Update config.env to the current CHAT_MODEL/CHAT_MODEL_VERSION defaults before deploying."
  }
}

variable "chat_model_version" {
  type    = string
  default = "2026-03-17"
}

variable "chat_sku" {
  type    = string
  default = "GlobalStandard"
}

variable "chat_capacity" {
  description = "Global Standard chat deployment capacity. REGDOCS enforces a production floor of 100 in main.tf to reduce Ask throttling."
  type        = number
  default     = 100
}

variable "ui_image_tag" {
  description = "Immutable source revision tag for the REGDOCS Atlas UI image."
  type        = string
  default     = "bootstrap"
}

variable "indexer_image_tag" {
  description = "Immutable source revision tag for the REGDOCS Atlas indexer image."
  type        = string
  default     = "bootstrap"
}

variable "deploy_workloads" {
  description = "Creates the Container App and indexing job after their ACR images exist."
  type        = bool
  default     = false
}

variable "tags" {
  type = map(string)
  default = {
    application = "regdocs-atlas"
    managed-by  = "terraform"
  }
}
