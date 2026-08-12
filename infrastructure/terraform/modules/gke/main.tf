/**
 * GKE Autopilot with workload identity.
 *
 * Autopilot removes node management entirely, which is the right trade for this
 * platform: nothing here needs a daemonset, a privileged container or a
 * specific kernel. The interface mirrors the EKS and AKS modules (ADR-0005).
 */

terraform {
  required_version = ">= 1.7"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = ">= 5.20"
    }
  }
}

locals {
  labels = merge(var.labels, { module = "gke" })
}

resource "google_container_cluster" "this" {
  name     = var.name
  project  = var.project_id
  location = var.region

  enable_autopilot = true

  network    = var.network
  subnetwork = var.subnetwork

  # Nodes have no public addresses; the control plane is reachable only from
  # the networks named below.
  private_cluster_config {
    enable_private_nodes    = true
    enable_private_endpoint = var.private_endpoint
    master_ipv4_cidr_block  = var.master_cidr
  }

  ip_allocation_policy {
    cluster_secondary_range_name  = var.pods_range_name
    services_secondary_range_name = var.services_range_name
  }

  master_authorized_networks_config {
    dynamic "cidr_blocks" {
      for_each = var.authorized_networks
      content {
        cidr_block   = cidr_blocks.value.cidr_block
        display_name = cidr_blocks.value.display_name
      }
    }
  }

  release_channel {
    channel = var.release_channel
  }

  # Workload identity is on by default under Autopilot; naming the pool
  # explicitly keeps the intent visible.
  workload_identity_config {
    workload_pool = "${var.project_id}.svc.id.goog"
  }

  deletion_protection = var.deletion_protection

  resource_labels = local.labels
}

resource "google_service_account" "workload" {
  account_id   = "${var.name}-workload"
  display_name = "Search metrics platform workload identity"
  project      = var.project_id
}

resource "google_service_account_iam_member" "workload" {
  service_account_id = google_service_account.workload.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "serviceAccount:${var.project_id}.svc.id.goog[${var.workload_namespace}/${var.workload_service_account}]"
}
