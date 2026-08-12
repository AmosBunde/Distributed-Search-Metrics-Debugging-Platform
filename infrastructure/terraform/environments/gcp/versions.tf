terraform {
  required_version = ">= 1.7"

  required_providers {
    google = {
      source = "hashicorp/google"
      # Pinned to a major version: provider majors change resource shapes, and
      # a plan that silently stops matching the code is worse than a version
      # bump you chose.
      version = "~> 6.0"
    }
    random = {
      source  = "hashicorp/random"
      version = ">= 3.6"
    }
  }

  backend "gcs" {}
}

provider "google" {
  project = var.project_id
  region  = var.region
}
