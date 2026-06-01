locals {
  cluster_control_plane_overrides = try(var.cluster.control_plane, {})
  cluster_control_plane_base = merge(
    {
      subnet_id = var.cluster.subnet_id
      version   = var.cluster.k8s_version
    },
    try(var.cluster.etcd_cluster_size, null) != null ? {
      etcd_cluster_size = var.cluster.etcd_cluster_size
    } : {}
  )
  cluster_control_plane_endpoints = merge(
    var.cluster.public_endpoint ? { public_endpoint = {} } : {},
    try(local.cluster_control_plane_overrides.endpoints, {})
  )
  cluster_control_plane = merge(
    local.cluster_control_plane_base,
    {
      for key, value in local.cluster_control_plane_overrides : key => value
      if key != "endpoints"
    },
    length(local.cluster_control_plane_endpoints) > 0 ? {
      endpoints = local.cluster_control_plane_endpoints
    } : {}
  )

  cluster_kube_network = merge(
    {
      service_cidrs = try(var.cluster.kube_network.service_cidrs, ["/20"])
    },
    try(var.cluster.kube_network, {})
  )

  gpu_clusters = {
    for key, cluster in var.gpu_clusters : key => cluster
    if try(cluster.enabled, true)
  }

  node_groups = {
    for key, group in var.node_groups : key => group
    if try(group.enabled, true)
  }

  node_group_autoscaling = {
    for key, group in local.node_groups : key => (
      try(group.autoscaling, null) == null || try(group.autoscaling.enabled, true) == false
      ? null
      : {
        min_node_count = group.autoscaling.min_node_count
        max_node_count = group.autoscaling.max_node_count
      }
    )
  }

  node_group_service_accounts_to_create = {
    for key, group in local.node_groups : key => group.service_account
    if length(trimspace(try(group.service_account.name != null ? group.service_account.name : "", ""))) > 0
  }

  node_group_service_account_ids = {
    for key, group in local.node_groups : key => (
      length(trimspace(try(group.service_account.id != null ? group.service_account.id : "", ""))) > 0
      ? trimspace(group.service_account.id)
      : try(nebius_iam_v1_service_account.node_group[key].id, null)
    )
  }

  node_group_cloud_init_user_data = {
    for key, group in local.node_groups : key => (
      try(length(group.ssh.public_keys) > 0, false)
      ? "#cloud-config\n${yamlencode({
        users = [
          {
            name                = group.ssh.username
            sudo                = "ALL=(ALL) NOPASSWD:ALL"
            shell               = "/bin/bash"
            ssh_authorized_keys = group.ssh.public_keys
          }
        ]
      })}"
      : null
    )
  }

  node_group_reservation_policies = {
    for key, group in local.node_groups : key => (
      try(group.gpu, false)
      ? {
        policy          = try(group.reservation.policy, "FORBID")
        reservation_ids = try(group.reservation.reservation_ids, [])
      }
      : null
    )
  }

  node_group_gpu_cluster_ids = {
    for key, group in local.node_groups : key => (
      length(trimspace(try(group.gpu_cluster_id != null ? group.gpu_cluster_id : "", ""))) > 0
      ? trimspace(group.gpu_cluster_id)
      : (
        length(trimspace(try(group.gpu_cluster_key != null ? group.gpu_cluster_key : "", ""))) > 0
        ? nebius_compute_v1_gpu_cluster.this[group.gpu_cluster_key].id
        : null
      )
    )
  }

  node_group_network_interfaces = {
    for key, group in local.node_groups : key => coalesce(
      try(group.network_interfaces, null),
      [
        merge(
          {
            subnet_id = coalesce(
              try(length(trimspace(group.subnet_id)) > 0 ? group.subnet_id : null, null),
              var.cluster.subnet_id,
            )
          },
          try(group.public_ips, false) ? {
            public_ip_address = {}
          } : {}
        )
      ]
    )
  }

  node_group_network_interface_subnet_refs = flatten([
    for key, interfaces in local.node_group_network_interfaces : [
      for index, interface in interfaces : {
        key       = "node_group_interface:${key}:${index}"
        subnet_id = try(interface.subnet_id, null)
      }
    ]
  ])

  subnet_refs_to_validate = merge(
    {
      cluster = var.cluster.subnet_id
    },
    {
      for key, group in local.node_groups :
      "node_group:${key}" => coalesce(
        try(length(trimspace(group.subnet_id)) > 0 ? group.subnet_id : null, null),
        var.cluster.subnet_id,
      )
    },
    {
      for ref in local.node_group_network_interface_subnet_refs :
      ref.key => ref.subnet_id
    }
  )

  node_group_metadata = {
    for key, group in local.node_groups : key => (
      length(try(group.node_labels, {})) > 0
      ? {
        labels = group.node_labels
      }
      : null
    )
  }

  node_group_templates = {
    for key, group in local.node_groups : key => merge(
      {
        resources = {
          platform = group.platform
          preset   = group.preset
        }
        network_interfaces = local.node_group_network_interfaces[key]
      },
      try(group.os, null) != null ? {
        os = group.os
      } : {},
      try(group.boot_disk, null) != null ? {
        boot_disk = group.boot_disk
      } : {},
      try(group.preemptible, false) ? {
        preemptible = {}
      } : {},
      length(try(group.taints, [])) > 0 ? {
        taints = group.taints
      } : {},
      length(try(group.filesystems, [])) > 0 ? {
        filesystems = group.filesystems
      } : {},
      try(group.local_disks, null) != null ? {
        local_disks = group.local_disks
      } : {},
      local.node_group_metadata[key] != null ? {
        metadata = local.node_group_metadata[key]
      } : {},
      try(group.gpu, false) && try(group.gpu_stack_source, "nebius_image") == "nebius_image" ? {
        gpu_settings = {
          drivers_preset = group.gpu_stack_preset
        }
      } : {},
      local.node_group_gpu_cluster_ids[key] != null ? {
        gpu_cluster = {
          id = local.node_group_gpu_cluster_ids[key]
        }
      } : {},
      local.node_group_reservation_policies[key] != null ? {
        reservation_policy = local.node_group_reservation_policies[key]
      } : {},
      local.node_group_service_account_ids[key] != null ? {
        service_account_id = local.node_group_service_account_ids[key]
      } : {},
      local.node_group_cloud_init_user_data[key] != null ? {
        cloud_init_user_data = local.node_group_cloud_init_user_data[key]
      } : {}
    )
  }
}
