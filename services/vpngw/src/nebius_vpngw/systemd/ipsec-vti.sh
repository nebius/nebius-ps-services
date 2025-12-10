#!/bin/bash
# Copyright 2022 Google LLC
# Licensed under the Apache License, Version 2.0
# 
# Custom updown script for strongSwan VTI creation with GCP HA VPN
# Originally from: https://cloud.google.com/community/tutorials/using-cloud-vpn-with-strongswan
# Adapted for nebius-vpngw

set -o nounset
set -o errexit

IP=$(which ip)

# Parse strongSwan mark values
PLUTO_MARK_OUT_ARR=(${PLUTO_MARK_OUT//// })
PLUTO_MARK_IN_ARR=(${PLUTO_MARK_IN//// })

# Arguments passed by nebius-vpngw agent
VTI_TUNNEL_ID=${1}
VTI_REMOTE=${2}
VTI_LOCAL=${3}

LOCAL_IF="${PLUTO_INTERFACE}"
VTI_IF="vti${VTI_TUNNEL_ID}"

# GCP's MTU is 1460, ipsec overhead is 73 bytes
GCP_MTU="1460"
VTI_MTU=$((GCP_MTU-73))

case "${PLUTO_VERB}" in
    up-client)
        # Create VTI interface with marks from strongSwan
        ${IP} link add ${VTI_IF} type vti local ${PLUTO_ME} remote ${PLUTO_PEER} \
            okey ${PLUTO_MARK_OUT_ARR[0]} ikey ${PLUTO_MARK_IN_ARR[0]}
        
        # Configure IP addresses
        ${IP} addr add ${VTI_LOCAL} remote ${VTI_REMOTE} dev "${VTI_IF}"
        ${IP} link set ${VTI_IF} up mtu ${VTI_MTU}

        # Disable IPSEC Policy on VTI (already encrypted)
        /sbin/sysctl -w net.ipv4.conf.${VTI_IF}.disable_policy=1

        # Enable loose source validation for asymmetric routing
        /sbin/sysctl -w net.ipv4.conf.${VTI_IF}.rp_filter=2 || \
            /sbin/sysctl -w net.ipv4.conf.${VTI_IF}.rp_filter=0

        # For specific peer subnets (not 0.0.0.0/0), add routes
        if [[ "${PLUTO_PEER_CLIENT}" != "0.0.0.0/0" ]]; then
            ${IP} route add "${PLUTO_PEER_CLIENT}" dev "${VTI_IF}"
        fi
        ;;
    down-client)
        # Remove VTI interface when tunnel goes down
        ${IP} tunnel del "${VTI_IF}"
        ;;
esac

# Enable IPv4 forwarding
/sbin/sysctl -w net.ipv4.ip_forward=1

# Disable IPSEC encryption on local interface (avoid double encryption)
/sbin/sysctl -w net.ipv4.conf.${LOCAL_IF}.disable_xfrm=1
/sbin/sysctl -w net.ipv4.conf.${LOCAL_IF}.disable_policy=1
