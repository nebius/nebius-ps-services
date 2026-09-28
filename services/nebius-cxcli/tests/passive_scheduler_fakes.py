"""Native-shaped desired and independently mutable observed passive settings."""

DESIRED_SCHEDULER = {
    "HealthCheckInterval": "120",
    "HealthCheckProgram": "/opt/slurm_scripts/hc_program.sh",
    "HealthCheckNodeState": "ANY,CYCLE",
    "Prolog": ["/opt/slurm_scripts/prolog.sh"],
    "Epilog": ["/opt/slurm_scripts/epilog.sh"],
}
LIVE_SCHEDULER = (
    "HealthCheckInterval = 120 sec\n"
    "HealthCheckProgram = /opt/slurm_scripts/hc_program.sh\n"
    "HealthCheckNodeState = CYCLE,ANY\n"
    "Prolog[0] = /opt/slurm_scripts/prolog.sh\n"
    "Epilog[0] = /opt/slurm_scripts/epilog.sh\n"
)
