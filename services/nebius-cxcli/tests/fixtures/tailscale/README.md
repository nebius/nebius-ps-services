# Tailscale observation fixtures

These are synthetic, public-safe responses, not captured tailnet data. They retain
the field names and JSON types consumed by cxcli from the pinned Tailscale
1.104.4 client and the hosted device/routes API. Unknown fields are intentionally
omitted. The node keys, node IDs, domain and addresses identify no real deployment.

Source contracts checked for this fixture:

- [Client status: PeerStatus and Status](https://github.com/tailscale/tailscale/blob/v1.104.4/ipn/ipnstate/ipnstate.go)
- [Preferences, including opt.Bool NoStatefulFiltering](https://github.com/tailscale/tailscale/blob/v1.104.4/ipn/prefs.go)
- [Device API response](https://github.com/tailscale/tailscale-client-go-v2/blob/v2.9.0/devices.go)
- [Advertised and enabled route fields](https://github.com/tailscale/tailscale/blob/v1.104.4/client/tailscale/routes.go)

Both peers have the approved subnet in `AllowedIPs`; only member a has it in
`PrimaryRoutes`. This distinction prevents tests from treating route approval as
client route selection. `NoStatefulFiltering` uses the upstream optional boolean
string representation. Advertised and enabled API routes are independent fields.

The tests replace command, HTTP, cloud and guest transports. They exercise the
real parsers and CLI dispatch without proving live routing, TLS, HA or API access.
