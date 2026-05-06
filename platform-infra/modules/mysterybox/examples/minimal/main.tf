variable "parent_id" {
  description = "Nebius project ID where the example MysteryBox secret is created."
  type        = string
  nullable    = false
}

variable "mysterybox_payload_values" {
  description = "Runtime payload values for the example MysteryBox secret."
  type        = map(map(string))
  default     = {}
  nullable    = false
  sensitive   = true
}

provider "nebius" {
  parent_id = var.parent_id
}

module "mysterybox" {
  source = "../.."

  parent_id = var.parent_id

  secrets = [
    {
      name       = "example-app-runtime"
      version_id = "n/a"
      payload = {
        API_KEY = {
          type = "text"
        }
        API_SECRET = {
          type = "text"
        }
      }
    }
  ]

  payload_values = var.mysterybox_payload_values
}
