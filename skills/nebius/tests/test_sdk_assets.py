"""Offline SDK 0.6.7 schema and safety tests. Install requirements-validation.txt."""

import contextlib
import hashlib
import importlib
import io
import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "assets"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from sdk.inventory import RESOURCES, metadata_inventory, resource_classes
from sdk.runtime import CloudError, MutationResult
from test_runtime import resource, response


class SchemaTests(unittest.TestCase):
    def test_every_inventory_api_and_request_is_real(self):
        for kind in RESOURCES:
            with self.subTest(kind=kind):
                client, request = resource_classes(kind)
                self.assertTrue(callable(client.list))
                self.assertIsInstance(
                    request(
                        parent_id="project-example", page_token="next"
                    ).SerializeToString(),
                    bytes,
                )

    def test_typed_private_request_builders_serialize(self):
        from compute.kubernetes import cluster_request, node_group_request
        from compute.virtual_machine import instance_request
        from nebius.api.nebius.compute.v1 import DiskSpec, FilesystemSpec
        from nebius.api.nebius.mk8s.v1 import (
            NetworkInterfaceTemplate,
            NodeTemplate,
            ResourcesSpec,
        )
        from nebius.api.nebius.msp.postgresql.v1alpha1 import (
            BackupSpec,
            BootstrapSpec,
            ConfigSpec,
        )
        from networking.network import (
            ingress_rule_request,
            network_request,
            security_group_request,
            subnet_request,
        )
        from storage.managed_services import postgresql_request, registry_request
        from storage.volumes import disk_request, filesystem_request

        requests = [
            instance_request(
                project_id="project-a",
                name="vm",
                platform="platform-a",
                preset="preset-a",
                disk_id="disk-a",
                subnet_id="subnet-a",
                security_group_id="group-a",
                cloud_init="#cloud-config\n",
            ),
            cluster_request(
                project_id="project-a",
                name="cluster",
                version="1.35",
                subnet_id="subnet-a",
                service_cidr="10.96.0.0/16",
            ),
            node_group_request(
                cluster_id="cluster-a",
                name="workers",
                version="1.35",
                count=1,
                template=NodeTemplate(
                    resources=ResourcesSpec(platform="platform-a", preset="preset-a"),
                    network_interfaces=[NetworkInterfaceTemplate(subnet_id="subnet-a")],
                ),
            ),
            disk_request(
                project_id="project-a", name="boot", image_id="image-a", size_gib=64
            ),
            filesystem_request(project_id="project-a", name="shared", size_gib=64),
            registry_request(project_id="project-a", name="images"),
            postgresql_request(
                project_id="project-a",
                name="db",
                network_id="network-a",
                config=ConfigSpec(version="17", public_access=False),
                bootstrap=BootstrapSpec(),
                backup=BackupSpec(
                    retention_policy="7d", backup_window_start="02:00:00"
                ),
            ),
            network_request(
                project_id="project-a", name="network", private_pool_id="pool-a"
            ),
            subnet_request(
                project_id="project-a",
                name="subnet",
                network_id="network-a",
                cidr="10.1.0.0/24",
            ),
            security_group_request(
                project_id="project-a", name="group", network_id="network-a"
            ),
            ingress_rule_request(
                group_id="group-a", name="ssh", source_cidr="10.1.0.0/24", port=22
            ),
        ]
        for request in requests:
            with self.subTest(request=type(request).__name__):
                self.assertIsInstance(request.SerializeToString(), bytes)
        self.assertEqual(requests[3].spec.type, DiskSpec.DiskType.NETWORK_SSD)
        self.assertEqual(
            requests[4].spec.type, FilesystemSpec.FilesystemType.NETWORK_SSD
        )
        self.assertNotIn(
            "publicIpAddress",
            json.loads(requests[0].to_json())["spec"]["networkInterfaces"][0],
        )
        self.assertNotIn(
            "endpoints", json.loads(requests[1].to_json())["spec"]["controlPlane"]
        )

    def test_builders_reject_unbounded_ingress_and_public_database(self):
        from networking.network import ingress_rule_request
        from storage.managed_services import postgresql_request

        with self.assertRaises(ValueError):
            ingress_rule_request(
                group_id="group-a", name="ssh", source_cidr="0.0.0.0/0", port=22
            )
        with self.assertRaises(ValueError):
            postgresql_request(
                project_id="project-a",
                name="db",
                network_id="network-a",
                config=NS(public_access=True),
                bootstrap=None,
                backup=None,
            )


class IdentityTests(unittest.TestCase):
    def test_group_membership_failure_preserves_pending_and_completed_ids(self):
        import iam.grant_project_roles as helper

        module = importlib.import_module("nebius.api.nebius.iam.v1")
        account = Mock()
        account.get.return_value = response(resource("sa-a"))
        pending = CloudError(
            "DEADLINE_EXCEEDED",
            operation_id="membership-operation",
            resource_id="membership-a",
            outcome="reconcile-required",
        )
        with (
            patch.object(module, "ServiceAccountServiceClient", return_value=account),
            patch.object(module, "GroupServiceClient", return_value=Mock()),
            patch.object(module, "GroupMembershipServiceClient", return_value=Mock()),
            patch.object(
                helper,
                "submit",
                side_effect=[MutationResult("group-operation", "group-a"), pending],
            ),
            self.assertRaises(CloudError) as caught,
        ):
            helper.create_dedicated_group(
                sdk=Mock(),
                project_id="project-a",
                service_account_id="sa-a",
                group_parent_id="tenant-a",
                group_name="dedicated",
            )
        error = caught.exception
        self.assertEqual(error.code, "DEADLINE_EXCEEDED")
        self.assertEqual(error.operation_id, "membership-operation")
        self.assertEqual(error.resource_id, "group-a")
        self.assertEqual(error.pending_resource_id, "membership-a")
        self.assertEqual(error.completed_resource_ids, ("group-a",))
        self.assertEqual(error.completed_operation_ids, ("group-operation",))

    def test_invalid_bucket_readiness_budget_never_calls_the_cloud(self):
        import storage.buckets as helper

        with (
            patch.object(helper, "rpc") as rpc,
            patch.object(helper, "submit") as submit,
            self.assertRaises(ValueError),
        ):
            helper.ensure_versioned_bucket(
                sdk=Mock(),
                project_id="project-a",
                bucket_name="example",
                ready_timeout=0,
            )
        rpc.assert_not_called()
        submit.assert_not_called()

    def test_inventory_allowlists_metadata_and_checks_parent(self):
        module = importlib.import_module("nebius.api.nebius.mysterybox.v1")
        item = resource()
        item.spec = NS(secret_version=NS(payload="secret-sentinel"))
        client = Mock()
        client.list.return_value = response(NS(items=[item], next_page_token=""))
        with patch.object(module, "SecretServiceClient", return_value=client):
            rows = metadata_inventory(Mock(), kind="secret", parent_id="project-a")
            self.assertNotIn("secret-sentinel", json.dumps(rows))
            item.metadata.parent_id = "other-project"
            with self.assertRaisesRegex(CloudError, "RESOURCE_IDENTITY_MISMATCH"):
                metadata_inventory(Mock(), kind="secret", parent_id="project-a")

    def test_service_account_name_collision_requires_explicit_adoption(self):
        import iam.create_service_account as helper

        module = importlib.import_module("nebius.api.nebius.iam.v1")
        client = Mock()
        client.get_by_name.return_value = response(resource(name="service"))
        with (
            patch.object(module, "ServiceAccountServiceClient", return_value=client),
            self.assertRaisesRegex(CloudError, "EXPLICIT"),
        ):
            helper.ensure_service_account(
                sdk=Mock(), project_id="project-a", service_account_name="service"
            )
        client.create.assert_not_called()

    def test_extra_group_member_never_receives_requested_grants(self):
        import iam.grant_project_roles as helper

        module = importlib.import_module("nebius.api.nebius.iam.v1")
        accounts, groups, members, permits = [Mock() for _ in range(4)]
        accounts.get.return_value = response(resource("sa-a"))
        groups.get.return_value = response(resource("group-a", parent="tenant-a"))
        own, unexpected = (
            resource("member-a", parent="group-a"),
            resource("member-b", parent="group-a"),
        )
        own.spec, unexpected.spec = NS(member_id="sa-a"), NS(member_id="other-account")
        members.list_members.side_effect = [
            response(NS(memberships=[own], next_page_token="two")),
            response(NS(memberships=[unexpected], next_page_token="")),
        ]
        with (
            patch.object(module, "ServiceAccountServiceClient", return_value=accounts),
            patch.object(module, "GroupServiceClient", return_value=groups),
            patch.object(module, "GroupMembershipServiceClient", return_value=members),
            patch.object(module, "AccessPermitServiceClient", return_value=permits),
            self.assertRaisesRegex(CloudError, "UNEXPECTED_GROUP_MEMBERSHIP"),
        ):
            helper.grant_service_account_project_roles(
                sdk=Mock(),
                project_id="project-a",
                service_account_id="sa-a",
                group_id="group-a",
                group_parent_id="tenant-a",
                role_ids=["viewer"],
            )
        permits.create.assert_not_called()

    def test_access_key_partial_success_resumes_without_second_create(self):
        import iam.create_access_key as helper

        iam = importlib.import_module("nebius.api.nebius.iam.v1")
        keys_api = importlib.import_module("nebius.api.nebius.iam.v2")
        accounts, keys = Mock(), Mock()
        accounts.get.return_value = response(resource("sa-a"))
        key = resource("key-a")
        key.spec = NS(account=NS(service_account=NS(id="sa-a")))
        keys.get.return_value = response(key)
        keys.get_secret.side_effect = [
            RuntimeError("secret-sentinel"),
            response(
                NS(aws_access_key_id="visible-sentinel", secret="secret-sentinel")
            ),
        ]
        with (
            patch.object(iam, "ServiceAccountServiceClient", return_value=accounts),
            patch.object(keys_api, "AccessKeyServiceClient", return_value=keys),
            patch.object(
                helper, "submit", return_value=MutationResult("operation-a", "key-a")
            ) as submit,
        ):
            with self.assertRaises(CloudError) as caught:
                helper.create_object_storage_access_key(
                    sdk=Mock(), project_id="project-a", service_account_id="sa-a"
                )
            self.assertEqual(caught.exception.resource_id, "key-a")
            self.assertEqual(caught.exception.operation_id, "operation-a")
            material = helper.create_object_storage_access_key(
                sdk=Mock(),
                project_id="project-a",
                service_account_id="sa-a",
                existing_access_key_id=caught.exception.resource_id,
            )
            submit.assert_called_once()
            self.assertNotIn("secret-sentinel", repr(material))
            self.assertNotIn("visible-sentinel", repr(material))

    def test_existing_bucket_versioning_mismatch_does_not_mutate(self):
        import storage.buckets as helper

        module = importlib.import_module("nebius.api.nebius.storage.v1")
        client = Mock()
        bucket = resource("bucket-a", name="state")
        bucket.spec = NS(versioning_policy=module.VersioningPolicy.DISABLED)
        client.get_by_name.return_value = response(bucket)
        client.get.return_value = response(bucket)
        with (
            patch.object(module, "BucketServiceClient", return_value=client),
            self.assertRaisesRegex(CloudError, "BUCKET_VERSIONING_MISMATCH"),
        ):
            helper.ensure_versioned_bucket(
                sdk=Mock(),
                project_id="project-a",
                bucket_name="state",
                existing_bucket_id="bucket-a",
            )
        client.create.assert_not_called()
        client.update.assert_not_called()

    def test_readback_mismatch_preserves_created_identity(self):
        import sdk.provision as helper

        client = Mock()
        client.get.return_value = response(resource("unexpected-id"))
        request = NS(metadata=NS(parent_id="project-a", name="example"))
        with (
            patch.object(
                helper,
                "submit",
                return_value=MutationResult("operation-a", "created-id"),
            ),
            self.assertRaises(CloudError) as caught,
        ):
            helper.create_and_verify(
                client, request, lambda **kw: NS(**kw), ready=lambda _: True
            )
        self.assertEqual(caught.exception.resource_id, "created-id")
        self.assertEqual(caught.exception.code, "RESOURCE_IDENTITY_MISMATCH")


class InspectorTests(unittest.TestCase):
    def test_real_quota_unknown_and_inactive_states_never_report_headroom(self):
        import inspect_quotas as helper
        from nebius.api.nebius.common.v1 import ResourceMetadata
        from nebius.api.nebius.quotas.v1 import (
            QuotaAllowance,
            QuotaAllowanceSpec,
            QuotaAllowanceStatus,
        )

        module = importlib.import_module("nebius.api.nebius.quotas.v1")
        states, usage_states = (
            QuotaAllowanceStatus.State,
            QuotaAllowanceStatus.UsageState,
        )
        cases = [
            (states.STATE_ACTIVE, usage_states.USAGE_STATE_UNKNOWN, None),
            (states.STATE_ACTIVE, usage_states.USAGE_STATE_UNSPECIFIED, None),
            (states.STATE_ACTIVE, usage_states.USAGE_STATE_NOT_APPLICABLE, None),
            (states.STATE_FROZEN, usage_states.USAGE_STATE_NOT_USED, None),
            (states.STATE_DELETED, usage_states.USAGE_STATE_USED, None),
            (states.STATE_PROVISIONING, usage_states.USAGE_STATE_USED, None),
            (states.STATE_ACTIVE, usage_states.USAGE_STATE_NOT_USED, 10),
        ]
        for state, usage_state, expected in cases:
            with self.subTest(state=state.name, usage_state=usage_state.name):
                item = QuotaAllowance(
                    metadata=ResourceMetadata(
                        id="quota-a", parent_id="project-a", name="gpu"
                    ),
                    spec=QuotaAllowanceSpec(limit=10, region="region-a"),
                    status=QuotaAllowanceStatus(state=state, usage_state=usage_state),
                )
                self.assertEqual(item.status.usage, 0)
                client = Mock()
                client.list.return_value = response(
                    NS(items=[item], next_page_token="")
                )
                with patch.object(
                    module, "QuotaAllowanceServiceClient", return_value=client
                ):
                    rows = helper._list_quotas(
                        Mock(), parent_id="project-a", scope="project"
                    )
                self.assertEqual(rows[("gpu", "region-a")].available, expected)
                effective = helper._effective_rows({}, rows)[0]
                self.assertEqual(effective["effective_available"], expected)
                if expected is None:
                    # An inactive/unmeasured constrained scope cannot borrow a
                    # healthy scope's headroom, even when its own limit is unset.
                    if state != states.STATE_ACTIVE:
                        item.spec.limit = None
                    healthy = helper.QuotaRow(
                        "tenant",
                        "gpu",
                        "region-a",
                        20,
                        0,
                        20,
                        "compute",
                        "",
                        "count",
                        "STATE_ACTIVE",
                        "USAGE_STATE_NOT_USED",
                        "0",
                    )
                    with patch.object(
                        module, "QuotaAllowanceServiceClient", return_value=client
                    ):
                        rows = helper._list_quotas(
                            Mock(), parent_id="project-a", scope="project"
                        )
                    effective = helper._effective_rows(
                        {("gpu", "region-a"): healthy}, rows
                    )[0]
                    self.assertIsNone(effective["effective_available"])

    def test_quota_mode_stays_explicit_for_empty_and_single_scope(self):
        import inspect_quotas as helper

        for raw in (False, True):
            output = io.StringIO()
            args = ["inspect_quotas", "--project-id", "project-a", "--json"] + (
                ["--raw"] if raw else []
            )
            with (
                patch.object(sys, "argv", args),
                patch.object(helper, "init_nebius_sdk", return_value=Mock()),
                patch.object(helper, "_list_quotas", return_value={}),
                contextlib.redirect_stdout(output),
            ):
                self.assertEqual(helper.main(), 0)
            payload = json.loads(output.getvalue())
            self.assertEqual(payload["mode"], "raw" if raw else "effective")
            self.assertEqual(payload["data"], [])

    def test_unknown_quota_usage_is_not_zero(self):
        import inspect_quotas as helper

        self.assertIsNone(helper._available(10, None))
        self.assertIsNone(helper._available(None, 2))
        self.assertEqual(helper._available(10, 4), 6)

        def quota(scope, limit, usage):
            return helper.QuotaRow(
                scope,
                "gpu",
                "region-a",
                limit,
                usage,
                helper._available(limit, usage),
                "compute",
                "",
                "count",
                "",
                "",
                "",
            )

        result = helper._effective_rows(
            {("gpu", "region-a"): quota("tenant", 10, None)},
            {("gpu", "region-a"): quota("project", 8, 2)},
        )
        self.assertIsNone(result[0]["effective_available"])
        self.assertEqual(result[0]["source_scope"], "unresolved")

    def test_quota_pages_and_parent_validation(self):
        import inspect_quotas as helper

        module = importlib.import_module("nebius.api.nebius.quotas.v1")
        first, second = (
            resource("quota-a", name="gpu"),
            resource("quota-b", name="disk"),
        )
        for item in (first, second):
            item.spec, item.status = NS(region="region-a", limit=10), NS(usage=None)
        client = Mock()

        def page(request, **kwargs):
            return response(
                NS(
                    items=[second] if request.page_token else [first],
                    next_page_token="" if request.page_token else "two",
                )
            )

        client.list.side_effect = page
        with patch.object(module, "QuotaAllowanceServiceClient", return_value=client):
            rows = helper._list_quotas(Mock(), parent_id="project-a", scope="project")
            self.assertEqual(len(rows), 2)
            self.assertIsNone(rows[("gpu", "region-a")].usage)
            second.metadata.parent_id = "other-project"
            with self.assertRaisesRegex(CloudError, "IDENTITY_MISMATCH"):
                helper._list_quotas(Mock(), parent_id="project-a", scope="project")

    def test_topology_private_pool_identity_and_subnet_membership(self):
        import inspect_vpc_topology as helper

        module = importlib.import_module("nebius.api.nebius.vpc.v1")
        network, pool, subnet = Mock(), Mock(), Mock()
        net = resource("network-a")
        net.spec = NS(ipv4_private_pools=NS(pools=[NS(id="pool-a")]))
        private_pool = resource("pool-a", parent="shared-parent")
        private_pool.spec = NS(
            version=module.IpVersion.IPV4,
            visibility=module.IpVisibility.PRIVATE,
            cidrs=[NS(cidr="10.1.0.0/16")],
            source_pool_id="",
        )
        sub = resource("subnet-a")
        sub.spec = NS(
            network_id="network-a", ipv4_private_pools=NS(use_network_pools=True)
        )
        sub.status = NS(ipv4_private_cidrs=[])
        network.list.return_value = response(NS(items=[net], next_page_token=""))
        pool.list.return_value = response(NS(items=[], next_page_token=""))
        pool.get.return_value = response(private_pool)
        subnet.list_by_network.return_value = response(
            NS(items=[sub], next_page_token="")
        )
        with (
            patch.object(module, "NetworkServiceClient", return_value=network),
            patch.object(module, "PoolServiceClient", return_value=pool),
            patch.object(module, "SubnetServiceClient", return_value=subnet),
            patch.object(helper, "init_nebius_sdk", return_value=Mock()),
        ):
            rows = helper._build_report("project-a", "network-a", None, None, None)
            self.assertEqual(rows[0]["parent_private_pools"][0]["pool_id"], "pool-a")
            sub.spec.network_id = "wrong-network"
            with self.assertRaisesRegex(CloudError, "SUBNET_NETWORK_MISMATCH"):
                helper._build_report("project-a", "network-a", None, None, None)
            sub.spec.network_id = "network-a"
            private_pool.metadata.id = "wrong-pool"
            with self.assertRaisesRegex(CloudError, "POOL_IDENTITY_MISMATCH"):
                helper._build_report("project-a", "network-a", None, None, None)
            private_pool.metadata.id = "pool-a"
            net.spec.ipv4_private_pools.pools = [NS(id="")]
            with self.assertRaisesRegex(CloudError, "POOL_REFERENCE_ID_MISSING"):
                helper._build_report("project-a", "network-a", None, None, None)

    def test_referenced_route_table_get_failure_is_not_empty_success(self):
        import inspect_vpc_routes as helper

        module = importlib.import_module("nebius.api.nebius.vpc.v1")
        network, table, route, subnet = [Mock() for _ in range(4)]
        network.list.return_value = response(
            NS(items=[resource("network-a")], next_page_token="")
        )
        table.list.return_value = response(NS(items=[], next_page_token=""))
        table.get.side_effect = RuntimeError("secret-sentinel")
        item = resource("subnet-a")
        item.spec = NS(network_id="network-a", route_table_id="table-a")
        item.status = NS(route_table=NS(id="table-a", default=False))
        subnet.list_by_network.return_value = response(
            NS(items=[item], next_page_token="")
        )
        with (
            patch.object(module, "NetworkServiceClient", return_value=network),
            patch.object(module, "RouteTableServiceClient", return_value=table),
            patch.object(module, "RouteServiceClient", return_value=route),
            patch.object(module, "SubnetServiceClient", return_value=subnet),
            patch.object(helper, "init_nebius_sdk", return_value=Mock()),
            self.assertRaises(CloudError),
        ):
            helper._build_report("project-a", None, None, None, None)

    def test_route_pages_and_cross_table_identity(self):
        import inspect_vpc_routes as helper

        module = importlib.import_module("nebius.api.nebius.vpc.v1")
        network, table, route, subnet = [Mock() for _ in range(4)]
        network.list.return_value = response(
            NS(items=[resource("network-a")], next_page_token="")
        )
        rt = resource("table-a")
        rt.spec = NS(network_id="network-a")
        table.list.return_value = response(NS(items=[rt], next_page_token=""))
        sub = resource("subnet-a")
        sub.spec = NS(network_id="network-a", route_table_id="table-a")
        sub.status = NS(route_table=NS(id="table-a", default=False))
        subnet.list_by_network.return_value = response(
            NS(items=[sub], next_page_token="")
        )
        first, second = (
            resource("route-a", parent="table-a"),
            resource("route-b", parent="table-a"),
        )
        for row in (first, second):
            row.spec = NS(
                destination=NS(cidr="10.2.0.0/16"),
                next_hop=NS(default_egress_gateway=True),
            )

        def page(request, **kwargs):
            return response(
                NS(
                    items=[second] if request.page_token else [first],
                    next_page_token="" if request.page_token else "two",
                )
            )

        route.list.side_effect = page
        with (
            patch.object(module, "NetworkServiceClient", return_value=network),
            patch.object(module, "RouteTableServiceClient", return_value=table),
            patch.object(module, "RouteServiceClient", return_value=route),
            patch.object(module, "SubnetServiceClient", return_value=subnet),
            patch.object(helper, "init_nebius_sdk", return_value=Mock()),
        ):
            rows = helper._build_report("project-a", None, None, None, None)
            self.assertEqual(len(rows[0]["route_tables"][0]["routes"]), 2)
            second.metadata.parent_id = "wrong-table"
            with self.assertRaisesRegex(CloudError, "IDENTITY_MISMATCH"):
                helper._build_report("project-a", None, None, None, None)
            second.metadata.parent_id = "table-a"
            rt.spec.network_id = "wrong-network"
            with self.assertRaisesRegex(CloudError, "ROUTE_TABLE_NETWORK_MISMATCH"):
                helper._build_report("project-a", None, None, None, None)


class ObjectIntegrityTests(unittest.TestCase):
    def test_read_uses_version_checks_hash_and_closes_stream(self):
        from storage.object_transfer import download_verified

        s3, body = Mock(), Mock()
        s3.get_object.return_value = {"Body": body}
        body.read.return_value = b"expected"
        digest = hashlib.sha256(b"expected").hexdigest()
        self.assertEqual(
            download_verified(
                s3,
                bucket="bucket-a",
                key="object",
                sha256=digest,
                version_id="version-a",
            ),
            b"expected",
        )
        self.assertEqual(s3.get_object.call_args.kwargs["VersionId"], "version-a")
        body.close.assert_called_once()
        with self.assertRaisesRegex(CloudError, "OBJECT_CHECKSUM_MISMATCH"):
            download_verified(s3, bucket="bucket-a", key="object", sha256="wrong")


if __name__ == "__main__":
    unittest.main()
