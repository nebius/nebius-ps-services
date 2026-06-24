from __future__ import annotations

import pytest

from nebius_cxcli.soperator_gpu_driver_jail import (
    SOPERATOR_GPU_DRIVER_JAIL_VALUE_KEY,
    ensure_soperator_gpu_driver_jail_values,
)


def test_soperator_gpu_driver_jail_enabled_for_gpu_nodeset_and_preserves_site_mounts() -> None:
    values = {
        "nodesets": [
            {
                "name": "worker-cpu",
                "slurmd": {"resources": {"gpu": 0}},
            },
            {
                "name": "worker-gpu",
                "gpu": {"enabled": True},
                "slurmd": {
                    "resources": {"gpu": 1},
                    "volumes": {
                        "customVolumeMounts": [
                            {
                                "name": "site-scratch",
                                "mountPath": "/site/scratch",
                                "volumeSource": {"hostPath": {"path": "/site/scratch"}},
                            }
                        ]
                    },
                },
            },
        ]
    }

    ensure_soperator_gpu_driver_jail_values(values, context="test target")

    assert values[SOPERATOR_GPU_DRIVER_JAIL_VALUE_KEY] == {"enabled": True}
    assert values["nodesets"][1]["slurmd"]["volumes"]["customVolumeMounts"] == [
        {
            "name": "site-scratch",
            "mountPath": "/site/scratch",
            "volumeSource": {"hostPath": {"path": "/site/scratch"}},
        }
    ]


def test_soperator_gpu_driver_jail_strips_adopted_canonical_raw_mount() -> None:
    values = {
        "nodesets": [
            {
                "name": "worker-gpu",
                "gpu": {"enabled": True},
                "slurmd": {
                    "volumes": {
                        "customVolumeMounts": [
                            {
                                "name": "nvidia-driver-root",
                                "mountPath": "/run/nvidia/driver",
                                "readOnly": False,
                                "volumeSource": {"hostPath": {"path": "/"}},
                            },
                            {
                                "name": "site-scratch",
                                "mountPath": "/site/scratch",
                                "volumeSource": {"hostPath": {"path": "/site/scratch"}},
                            },
                        ]
                    }
                },
            }
        ]
    }

    ensure_soperator_gpu_driver_jail_values(values, context="adopted target")

    assert values["nodesets"][0]["slurmd"]["volumes"]["customVolumeMounts"] == [
        {
            "name": "site-scratch",
            "mountPath": "/site/scratch",
            "volumeSource": {"hostPath": {"path": "/site/scratch"}},
        }
    ]


@pytest.mark.parametrize(
    ("values", "message"),
    [
        (
            {
                "gpuDriverJail": {"enabled": False},
                "nodesets": [{"name": "worker-gpu", "gpu": {"enabled": True}}],
            },
            "gpuDriverJail.enabled=false",
        ),
        (
            {
                "nodesets": [
                    {
                        "name": "worker-gpu",
                        "gpu": {"enabled": True},
                        "slurmd": {
                            "volumes": {
                                "customVolumeMounts": [
                                    {
                                        "name": "nvidia-driver-root",
                                        "mountPath": "/other",
                                        "volumeSource": {"hostPath": {"path": "/"}},
                                    }
                                ]
                            }
                        },
                    }
                ]
            },
            "conflicting customVolumeMount",
        ),
        (
            {
                "nodesets": [
                    {
                        "name": "worker-gpu",
                        "gpu": {"enabled": True},
                        "slurmd": {
                            "volumes": {
                                "customVolumeMounts": [
                                    {
                                        "name": "site-driver",
                                        "mountPath": "/run/nvidia/driver",
                                        "volumeSource": {"hostPath": {"path": "/"}},
                                    }
                                ]
                            }
                        },
                    }
                ]
            },
            "conflicting customVolumeMount",
        ),
    ],
)
def test_soperator_gpu_driver_jail_rejects_disabled_or_conflicting_values(
    values: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        ensure_soperator_gpu_driver_jail_values(values, context="test target")
