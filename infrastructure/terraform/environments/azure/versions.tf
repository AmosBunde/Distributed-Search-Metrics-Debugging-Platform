terraform {
  required_version = ">= 1.7"

  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 4.0"
    }
    random = {
      source  = "hashicorp/random"
      version = ">= 3.6"
    }
  }

  # State in a storage account container, supplied with -backend-config.
  backend "azurerm" {}
}

provider "azurerm" {
  features {
    resource_group {
      # Refuse to delete a resource group that still holds resources: the
      # single most destructive accident available in Azure.
      prevent_deletion_if_contains_resources = true
    }
  }
}
