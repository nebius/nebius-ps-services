provider "nebius" {
  service_account = {
    private_key_file_env = "NB_AUTHKEY_PRIVATE_PATH"
    public_key_id_env    = "NB_AUTHKEY_PUBLIC_ID"
    account_id_env       = "NB_SA_ID"
  }
}
