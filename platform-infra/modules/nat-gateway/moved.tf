# Preserve owned resources when introducing the explicit create/reuse selector.
moved {
  from = nebius_vpc_v1_subnet.gateway
  to   = nebius_vpc_v1_subnet.gateway[0]
}
moved {
  from = nebius_vpc_v1_route_table.gateway
  to   = nebius_vpc_v1_route_table.gateway[0]
}
moved {
  from = nebius_vpc_v1_route.gateway_egress
  to   = nebius_vpc_v1_route.gateway_egress[0]
}
