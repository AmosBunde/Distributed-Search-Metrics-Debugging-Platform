/**
 * GCP environment.
 *
 * The same platform again, in GCP's vocabulary. Two differences are worth
 * knowing rather than discovering: Pub/Sub is not Kafka, so a connector is
 * needed to keep the application unchanged, and GKE Autopilot manages nodes
 * itself, so node sizing lives in pod requests instead of machine types.
 */

locals {
  name    = var.name
  is_prod = var.environment == "prod"

  labels = {
    project     = "search-metrics"
    environment = var.environment
    managed_by  = "terraform"
  }
}

# --- Network ----------------------------------------------------------------

resource "google_compute_network" "this" {
  name                    = "${local.name}-vpc"
  project                 = var.project_id
  auto_create_subnetworks = false
}

resource "google_compute_subnetwork" "this" {
  name          = "${local.name}-subnet"
  project       = var.project_id
  region        = var.region
  network       = google_compute_network.this.id
  ip_cidr_range = var.subnet_cidr

  # Private Google Access lets nodes without public IPs reach Artifact
  # Registry and the other Google APIs.
  private_ip_google_access = true

  secondary_ip_range {
    range_name    = "pods"
    ip_cidr_range = var.pods_cidr
  }

  secondary_ip_range {
    range_name    = "services"
    ip_cidr_range = var.services_cidr
  }
}

resource "google_compute_router" "this" {
  name    = "${local.name}-router"
  project = var.project_id
  region  = var.region
  network = google_compute_network.this.id
}

resource "google_compute_router_nat" "this" {
  name                               = "${local.name}-nat"
  project                            = var.project_id
  region                             = var.region
  router                             = google_compute_router.this.name
  nat_ip_allocate_option             = "AUTO_ONLY"
  source_subnetwork_ip_ranges_to_nat = "ALL_SUBNETWORKS_ALL_IP_RANGES"

  log_config {
    enable = true
    filter = "ERRORS_ONLY"
  }
}

# --- Kubernetes -------------------------------------------------------------

module "gke" {
  source = "../../modules/gke"

  name       = local.name
  project_id = var.project_id
  region     = var.region

  network             = google_compute_network.this.id
  subnetwork          = google_compute_subnetwork.this.id
  pods_range_name     = "pods"
  services_range_name = "services"

  authorized_networks = var.authorized_networks
  deletion_protection = local.is_prod

  labels = local.labels
}

# --- Pub/Sub ----------------------------------------------------------------
# Pub/Sub is not Kafka. The platform speaks the Kafka protocol, so a connector
# bridges the two rather than the application being rewritten per cloud
# (ADR-0005). The topics exist here so retention and access are managed as
# infrastructure.

resource "google_pubsub_topic" "topics" {
  for_each = toset([
    "search.events",
    "search.results",
    "search.errors",
    "search.anomalies",
    "search.spans",
  ])

  name    = each.value
  project = var.project_id
  labels  = local.labels

  message_retention_duration = "604800s" # 7 days, matching MSK and Event Hubs
}

resource "google_pubsub_subscription" "engine" {
  for_each = google_pubsub_topic.topics

  name    = "${each.value.name}.metrics-engine"
  topic   = each.value.id
  project = var.project_id
  labels  = local.labels

  ack_deadline_seconds       = 60
  message_retention_duration = "604800s"
  retain_acked_messages      = false

  retry_policy {
    minimum_backoff = "10s"
    maximum_backoff = "600s"
  }
}

# --- Cloud SQL --------------------------------------------------------------

resource "google_compute_global_address" "private_services" {
  name          = "${local.name}-private-services"
  project       = var.project_id
  purpose       = "VPC_PEERING"
  address_type  = "INTERNAL"
  prefix_length = 16
  network       = google_compute_network.this.id
}

resource "google_service_networking_connection" "private_services" {
  network                 = google_compute_network.this.id
  service                 = "servicenetworking.googleapis.com"
  reserved_peering_ranges = [google_compute_global_address.private_services.name]
}

resource "google_sql_database_instance" "postgres" {
  name             = "${local.name}-postgres"
  project          = var.project_id
  region           = var.region
  database_version = "POSTGRES_15"

  # Cloud SQL keeps an instance name reserved after deletion, so a rebuild
  # under the same name fails unless this is deliberate.
  deletion_protection = local.is_prod

  settings {
    tier              = var.postgres_tier
    availability_type = local.is_prod ? "REGIONAL" : "ZONAL"
    disk_type         = "PD_SSD"
    disk_size         = 50
    disk_autoresize   = true

    ip_configuration {
      ipv4_enabled                                  = false
      private_network                               = google_compute_network.this.id
      enable_private_path_for_google_cloud_services = true
    }

    backup_configuration {
      enabled                        = true
      start_time                     = "03:00"
      point_in_time_recovery_enabled = local.is_prod
      transaction_log_retention_days = local.is_prod ? 7 : 1
    }

    insights_config {
      query_insights_enabled = true
    }

    user_labels = local.labels
  }

  depends_on = [google_service_networking_connection.private_services]
}

resource "google_sql_database" "meta" {
  name     = "search_metrics_meta"
  project  = var.project_id
  instance = google_sql_database_instance.postgres.name
}

resource "google_sql_user" "search" {
  name     = "search"
  project  = var.project_id
  instance = google_sql_database_instance.postgres.name
  password = var.db_password
}

# --- Memorystore ------------------------------------------------------------

resource "google_redis_instance" "this" {
  name           = "${local.name}-redis"
  project        = var.project_id
  region         = var.region
  tier           = local.is_prod ? "STANDARD_HA" : "BASIC"
  memory_size_gb = var.redis_memory_gb
  redis_version  = "REDIS_7_0"

  authorized_network = google_compute_network.this.id
  connect_mode       = "PRIVATE_SERVICE_ACCESS"

  # TLS and AUTH, matching the other two clouds.
  transit_encryption_mode = "SERVER_AUTHENTICATION"
  auth_enabled            = true

  labels     = local.labels
  depends_on = [google_service_networking_connection.private_services]
}

# --- Artifact Registry and storage ------------------------------------------

resource "google_artifact_registry_repository" "this" {
  location      = var.region
  project       = var.project_id
  repository_id = "search-metrics"
  format        = "DOCKER"
  description   = "Search metrics platform images"
  labels        = local.labels
}

resource "random_id" "bucket_suffix" {
  byte_length = 4
}

resource "google_storage_bucket" "telemetry" {
  name     = "${local.name}-telemetry-${random_id.bucket_suffix.hex}"
  project  = var.project_id
  location = var.region

  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"

  versioning {
    enabled = local.is_prod
  }

  lifecycle_rule {
    condition {
      age = 30
    }
    action {
      type          = "SetStorageClass"
      storage_class = "NEARLINE"
    }
  }

  lifecycle_rule {
    condition {
      age = 90
    }
    action {
      type          = "SetStorageClass"
      storage_class = "COLDLINE"
    }
  }

  lifecycle_rule {
    condition {
      age = 365
    }
    action {
      type = "Delete"
    }
  }

  labels = local.labels
}

# --- Application identity ---------------------------------------------------

resource "google_storage_bucket_iam_member" "telemetry" {
  bucket = google_storage_bucket.telemetry.name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${module.gke.workload_service_account_email}"
}

resource "google_project_iam_member" "pubsub" {
  for_each = toset(["roles/pubsub.publisher", "roles/pubsub.subscriber"])

  project = var.project_id
  role    = each.value
  member  = "serviceAccount:${module.gke.workload_service_account_email}"
}

resource "google_project_iam_member" "sql_client" {
  project = var.project_id
  role    = "roles/cloudsql.client"
  member  = "serviceAccount:${module.gke.workload_service_account_email}"
}
