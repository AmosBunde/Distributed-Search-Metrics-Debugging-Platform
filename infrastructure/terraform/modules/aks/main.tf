/**
 * AKS cluster with workload identity.
 *
 * Workload identity is Azure's answer to IRSA: a pod's service account
 * federates to a managed identity, so no static credentials exist. The module
 * interface deliberately mirrors the EKS module (ADR-0005) — same inputs, same
 * outputs by role — so the Helm values are the only thing that differs at
 * deploy time.
 */

terraform {
  required_version = ">= 1.7"
  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 4.0"
    }
  }
}

locals {
  tags = merge(var.tags, { Module = "aks" })
}

resource "azurerm_log_analytics_workspace" "this" {
  name                = "${var.name}-logs"
  location            = var.location
  resource_group_name = var.resource_group_name
  sku                 = "PerGB2018"
  retention_in_days   = var.log_retention_days
  tags                = local.tags
}

resource "azurerm_kubernetes_cluster" "this" {
  name                = var.name
  location            = var.location
  resource_group_name = var.resource_group_name
  dns_prefix          = var.name
  kubernetes_version  = var.kubernetes_version

  # Private by default, for the same reason the EKS endpoint is.
  private_cluster_enabled = var.private_cluster
  local_account_disabled  = var.private_cluster

  # Federated identity rather than a secret in the cluster.
  workload_identity_enabled = true
  oidc_issuer_enabled       = true

  default_node_pool {
    name                 = "general"
    vm_size              = var.node_vm_size
    vnet_subnet_id       = var.subnet_id
    auto_scaling_enabled = true
    min_count            = var.node_min_count
    max_count            = var.node_max_count
    max_pods             = 60
    os_disk_size_gb      = 100
    zones                = var.availability_zones
    upgrade_settings {
      max_surge = "25%"
    }
    node_labels = { workload = "general" }
  }

  identity {
    type = "SystemAssigned"
  }

  network_profile {
    network_plugin    = "azure"
    network_policy    = "calico"
    load_balancer_sku = "standard"
    outbound_type     = "loadBalancer"
    service_cidr      = var.service_cidr
    dns_service_ip    = var.dns_service_ip
  }

  oms_agent {
    log_analytics_workspace_id = azurerm_log_analytics_workspace.this.id
  }

  azure_policy_enabled = true

  lifecycle {
    # The autoscaler owns node count after the first apply.
    ignore_changes = [default_node_pool[0].node_count]
  }

  tags = local.tags
}

resource "azurerm_user_assigned_identity" "workload" {
  name                = "${var.name}-workload"
  location            = var.location
  resource_group_name = var.resource_group_name
  tags                = local.tags
}

resource "azurerm_federated_identity_credential" "workload" {
  name                = "${var.name}-workload"
  resource_group_name = var.resource_group_name
  parent_id           = azurerm_user_assigned_identity.workload.id
  audience            = ["api://AzureADTokenExchange"]
  issuer              = azurerm_kubernetes_cluster.this.oidc_issuer_url
  subject             = "system:serviceaccount:${var.workload_namespace}:${var.workload_service_account}"
}
