#!/bin/bash
# Fix routing issues that can break VPN connectivity
# This script removes the table 220 routing rule that overrides VTI routes

# Remove table 220 routing rule if it exists
if ip rule list | grep -q "lookup 220"; then
    logger -t nebius-vpngw "Removing table 220 routing rule"
    ip rule del table 220 2>/dev/null
    if ! ip rule list | grep -q "lookup 220"; then
        logger -t nebius-vpngw "Successfully removed table 220 routing rule"
    fi
fi

# Remove broad 169.254.0.0/16 route if it exists via eth0
if ip route show 169.254.0.0/16 | grep -q "eth0"; then
    logger -t nebius-vpngw "Removing broad 169.254.0.0/16 route via eth0"
    ip route del 169.254.0.0/16 2>/dev/null
    if ! ip route show 169.254.0.0/16 | grep -q "."; then
        logger -t nebius-vpngw "Successfully removed 169.254.0.0/16 route"
    fi
fi
