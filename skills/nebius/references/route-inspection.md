# Nebius Route Inspection

## Read This When

- the task depends on which subnets consume which route tables
- a user wants to know whether a route comes from a default or custom route table
- you need to verify safe routing behavior against live Nebius state

## Mental Model

- A subnet has an effective route table in `subnet.status.route_table`.
- `subnet.status.route_table.default=true` means the subnet is still attached to the default route table.
- A subnet may have `spec.route_table_id` for a custom attachment, but the effective state should come from subnet status.
- Routes are listed under a route table with `ListRoutes(parent_id=<route_table_id>)`.

## Safe Interpretation

- route ownership is best understood as **route table attachment plus route table contents**
- do not infer route ownership from subnet CIDRs alone
- when reviewing route automation, verify both:
  - which subnets were selected
  - which route tables those subnets actually consume

## Helper Scripts

Use:

```bash
python scripts/inspect_vpc_topology.py --project-id <project-id>
python scripts/inspect_vpc_routes.py --project-id <project-id>
```

These scripts report:

- subnet allocation mode
- subnet effective route table attachment
- route table consumers
- routes and next-hop summaries
