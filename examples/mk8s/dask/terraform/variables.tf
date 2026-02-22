variable "tenant_id" {
  description = "Tenant ID."
  type        = string
}

variable "parent_id" {
  description = "Project ID."
  type        = string
}

variable "region" {
  description = "The current region."
  type        = string
}

variable "subnet_id" {
  description = "Subnet ID."
  type        = string
}

variable "ssh_user_name" {
  type        = string
  description = "Username for SSH."
}

variable "ssh_public_key" {
  type        = string
  description = "Public SSH key."
}

variable "run_version" {
  description = "Version suffix."
  type        = string
}
