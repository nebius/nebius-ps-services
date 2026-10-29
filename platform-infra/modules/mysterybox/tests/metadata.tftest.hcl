mock_provider "nebius" {}

run "metadata_only" {
  command = plan
  variables {
    parent_id               = "project-example"
    create_initial_versions = false
    secrets                 = [{ name = "vpn-login" }, { name = "vpn-host" }, { name = "vpn-connection" }]
  }
  assert {
    condition     = length(nebius_mysterybox_v1_secret.this) == 3 && length(nebius_mysterybox_v1_secret_version.this) == 0
    error_message = "Metadata-only declarations must create exactly three containers and no versions."
  }
  assert {
    condition     = length(output.secret_version_ids) == 0 && length(output.primary_secret_version_ids) == 0
    error_message = "The external payload owner must keep sole ownership of versions."
  }
}

run "application_payload" {
  command = plan
  variables {
    parent_id      = "project-example"
    secrets        = [{ name = "application", payload = { API_KEY = { type = "text" } } }]
    payload_values = { application = { API_KEY = "test-fixture" } }
  }
  assert {
    condition     = length(nebius_mysterybox_v1_secret_version.this) == 1
    error_message = "The ordinary application initial-version path must remain available."
  }
}

run "reject_metadata_payload" {
  command = plan
  variables {
    parent_id               = "project-example"
    create_initial_versions = false
    secrets                 = [{ name = "vpn-login", payload = { KEY = { type = "text" } } }]
  }
  expect_failures = [var.secrets]
}
