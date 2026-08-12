/**
 * Azure environment.
 *
 * The same platform as the AWS environment, expressed in Azure's vocabulary:
 * Event Hubs speaks the Kafka protocol, Flexible Server is PostgreSQL, Azure
 * Cache is Redis. The module interfaces match so the Helm values are the only
 * thing that differs at deploy time (ADR-0005).
 */

locals {
  name    = var.name
  is_prod = var.environment == "prod"

  tags = {
    Project     = "search-metrics"
    Environment = var.environment
    ManagedBy   = "terraform"
  }
}

resource "azurerm_resource_group" "this" {
  name     = "${local.name}-rg"
  location = var.location
  tags     = local.tags
}

# --- Network ----------------------------------------------------------------

resource "azurerm_virtual_network" "this" {
  name                = "${local.name}-vnet"
  location            = azurerm_resource_group.this.location
  resource_group_name = azurerm_resource_group.this.name
  address_space       = [var.vnet_cidr]
  tags                = local.tags
}

resource "azurerm_subnet" "aks" {
  name                 = "aks"
  resource_group_name  = azurerm_resource_group.this.name
  virtual_network_name = azurerm_virtual_network.this.name
  address_prefixes     = [cidrsubnet(var.vnet_cidr, 4, 0)]
}

resource "azurerm_subnet" "data" {
  name                 = "data"
  resource_group_name  = azurerm_resource_group.this.name
  virtual_network_name = azurerm_virtual_network.this.name
  address_prefixes     = [cidrsubnet(var.vnet_cidr, 8, 32)]

  # Flexible Server is injected into a delegated subnet rather than reached
  # over a public endpoint.
  delegation {
    name = "postgres"
    service_delegation {
      name    = "Microsoft.DBforPostgreSQL/flexibleServers"
      actions = ["Microsoft.Network/virtualNetworks/subnets/join/action"]
    }
  }
}

resource "azurerm_network_security_group" "data" {
  name                = "${local.name}-data-nsg"
  location            = azurerm_resource_group.this.location
  resource_group_name = azurerm_resource_group.this.name
  tags                = local.tags

  security_rule {
    name                       = "allow-aks-inbound"
    priority                   = 100
    direction                  = "Inbound"
    access                     = "Allow"
    protocol                   = "Tcp"
    source_port_range          = "*"
    destination_port_ranges    = ["5432", "6380", "8123", "9092"]
    source_address_prefix      = cidrsubnet(var.vnet_cidr, 4, 0)
    destination_address_prefix = "*"
  }

  security_rule {
    name                       = "deny-everything-else"
    priority                   = 4096
    direction                  = "Inbound"
    access                     = "Deny"
    protocol                   = "*"
    source_port_range          = "*"
    destination_port_range     = "*"
    source_address_prefix      = "*"
    destination_address_prefix = "*"
  }
}

resource "azurerm_subnet_network_security_group_association" "data" {
  subnet_id                 = azurerm_subnet.data.id
  network_security_group_id = azurerm_network_security_group.data.id
}

# --- Kubernetes -------------------------------------------------------------

module "aks" {
  source = "../../modules/aks"

  name                = local.name
  location            = var.location
  resource_group_name = azurerm_resource_group.this.name
  kubernetes_version  = var.kubernetes_version
  subnet_id           = azurerm_subnet.aks.id

  node_vm_size   = var.node_vm_size
  node_min_count = var.node_min_count
  node_max_count = var.node_max_count

  log_retention_days = local.is_prod ? 90 : 30
  tags               = local.tags
}

# --- Event Hubs (Kafka protocol) --------------------------------------------

resource "azurerm_eventhub_namespace" "this" {
  name                = "${local.name}-events"
  location            = azurerm_resource_group.this.location
  resource_group_name = azurerm_resource_group.this.name
  sku                 = "Standard"
  capacity            = var.eventhub_capacity

  # Ingest is bursty by nature; auto-inflate absorbs a spike instead of
  # throttling the collector.
  auto_inflate_enabled     = true
  maximum_throughput_units = var.eventhub_max_capacity

  tags = local.tags
}

resource "azurerm_eventhub" "topics" {
  for_each = toset([
    "search.events",
    "search.results",
    "search.errors",
    "search.anomalies",
    "search.spans",
  ])

  name                = each.value
  namespace_name      = azurerm_eventhub_namespace.this.name
  resource_group_name = azurerm_resource_group.this.name
  partition_count     = 6
  message_retention   = 7
}

resource "azurerm_eventhub_namespace_authorization_rule" "platform" {
  name                = "platform"
  namespace_name      = azurerm_eventhub_namespace.this.name
  resource_group_name = azurerm_resource_group.this.name

  listen = true
  send   = true
  manage = false
}

# --- PostgreSQL -------------------------------------------------------------

resource "azurerm_private_dns_zone" "postgres" {
  name                = "${local.name}.postgres.database.azure.com"
  resource_group_name = azurerm_resource_group.this.name
  tags                = local.tags
}

resource "azurerm_private_dns_zone_virtual_network_link" "postgres" {
  name                  = "${local.name}-postgres"
  resource_group_name   = azurerm_resource_group.this.name
  private_dns_zone_name = azurerm_private_dns_zone.postgres.name
  virtual_network_id    = azurerm_virtual_network.this.id
  tags                  = local.tags
}

resource "azurerm_postgresql_flexible_server" "this" {
  name                = "${local.name}-postgres"
  location            = azurerm_resource_group.this.location
  resource_group_name = azurerm_resource_group.this.name
  version             = "15"
  sku_name            = var.postgres_sku

  delegated_subnet_id           = azurerm_subnet.data.id
  private_dns_zone_id           = azurerm_private_dns_zone.postgres.id
  public_network_access_enabled = false

  administrator_login    = "search"
  administrator_password = var.db_password

  storage_mb                   = 65536
  backup_retention_days        = local.is_prod ? 7 : 1
  geo_redundant_backup_enabled = local.is_prod

  dynamic "high_availability" {
    for_each = local.is_prod ? [1] : []
    content {
      mode = "ZoneRedundant"
    }
  }

  tags       = local.tags
  depends_on = [azurerm_private_dns_zone_virtual_network_link.postgres]
}

resource "azurerm_postgresql_flexible_server_database" "meta" {
  name      = "search_metrics_meta"
  server_id = azurerm_postgresql_flexible_server.this.id
  charset   = "UTF8"
  collation = "en_US.utf8"
}

# --- Redis ------------------------------------------------------------------

resource "azurerm_redis_cache" "this" {
  name                = "${local.name}-redis"
  location            = azurerm_resource_group.this.location
  resource_group_name = azurerm_resource_group.this.name
  capacity            = var.redis_capacity
  family              = "C"
  sku_name            = "Standard"

  # TLS only, matching the AWS environment: a VNet is not a trust boundary.
  non_ssl_port_enabled = false
  minimum_tls_version  = "1.2"

  tags = local.tags
}

# --- Registry and storage ---------------------------------------------------

resource "azurerm_container_registry" "this" {
  name                = replace("${local.name}acr", "-", "")
  location            = azurerm_resource_group.this.location
  resource_group_name = azurerm_resource_group.this.name
  sku                 = "Premium"
  admin_enabled       = false
  tags                = local.tags
}

resource "azurerm_role_assignment" "acr_pull" {
  scope                = azurerm_container_registry.this.id
  role_definition_name = "AcrPull"
  principal_id         = module.aks.kubelet_identity_object_id
}

resource "random_string" "storage_suffix" {
  length  = 6
  special = false
  upper   = false
}

resource "azurerm_storage_account" "telemetry" {
  name                = "${replace(local.name, "-", "")}${random_string.storage_suffix.result}"
  location            = azurerm_resource_group.this.location
  resource_group_name = azurerm_resource_group.this.name

  account_tier             = "Standard"
  account_replication_type = local.is_prod ? "GRS" : "LRS"
  min_tls_version          = "TLS1_2"

  https_traffic_only_enabled      = true
  allow_nested_items_to_be_public = false

  blob_properties {
    delete_retention_policy {
      days = local.is_prod ? 30 : 7
    }
  }

  tags = local.tags
}

resource "azurerm_storage_container" "telemetry" {
  name                  = "telemetry"
  storage_account_id    = azurerm_storage_account.telemetry.id
  container_access_type = "private"
}

resource "azurerm_storage_management_policy" "telemetry" {
  storage_account_id = azurerm_storage_account.telemetry.id

  rule {
    name    = "tier-and-expire"
    enabled = true

    filters {
      blob_types = ["blockBlob"]
    }

    actions {
      base_blob {
        tier_to_cool_after_days_since_modification_greater_than    = 30
        tier_to_archive_after_days_since_modification_greater_than = 90
        delete_after_days_since_modification_greater_than          = 365
      }
    }
  }
}

resource "azurerm_role_assignment" "storage" {
  scope                = azurerm_storage_account.telemetry.id
  role_definition_name = "Storage Blob Data Contributor"
  principal_id         = module.aks.workload_identity_principal_id
}
