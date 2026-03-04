module "mysterybox" {
  source = "../.."

  parent_id = "project-xxxxxxxx"

  secrets = {
    app = {
      name         = "example-app-runtime"
      payload_keys = ["API_KEY", "API_SECRET"]
      labels = {
        scope = "apps"
      }
    }
  }

  secret_values = {
    app = {
      API_KEY    = "replace-me"
      API_SECRET = "replace-me"
    }
  }
}
