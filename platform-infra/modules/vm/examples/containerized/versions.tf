terraform {
  required_version = ">= 1.10.0, < 2.0.0"

  required_providers {
    nebius = {
      source  = "terraform-provider.storage.eu-north1.nebius.cloud/nebius/nebius"
      version = ">= 0.5.217, < 0.6.0"
    }
  }
}
