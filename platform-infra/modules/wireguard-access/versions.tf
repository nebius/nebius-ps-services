terraform {
  required_version = ">= 1.11.0, < 2.0.0"
  required_providers {
    nebius = {
      source  = "nebius/nebius"
      version = ">= 0.6.76, < 0.7.0"
    }
  }
}
