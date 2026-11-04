terraform {
  required_version = ">= 1.10.0"
  required_providers {
    nebius = {
      source  = "nebius/nebius"
      version = ">= 0.6.77, < 0.7.0"
    }
  }
}
