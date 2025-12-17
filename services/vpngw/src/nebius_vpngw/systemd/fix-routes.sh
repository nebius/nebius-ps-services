#!/bin/bash
# Fix routing issues that can break VPN connectivity

# Remove table 220 routing rule and flush the table if it exists
if ip rule list | grep -q "lookup 220"; then
    logger -t nebius-vpngw "Removing table 220 routing rule"
    ip route flush table 220 2>/dev/null
    ip rule del lookup 220 2>/dev/null
    ip rule del pref 220 2>/dev/null
fi

# Remove broad 169.254.0.0/16 route if it exists via eth0 (keep metadata-specific routes)
if ip route show 169.254.0.0/16 | grep -q "eth0"; then
    logger -t nebius-vpngw "Removing broad 169.254.0.0/16 route via eth0"
    ip route del 169.254.0.0/16 2>/dev/null
fi
