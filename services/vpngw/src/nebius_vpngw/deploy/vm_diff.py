from __future__ import annotations

import typing as t
from dataclasses import dataclass
from enum import Enum


class ChangeType(Enum):
    """Classification of configuration changes."""
    NO_CHANGE = "no_change"
    SAFE = "safe"              # Can apply via SSH config push (tunnel configs, PSKs, routing)
    DESTRUCTIVE = "destructive"  # Requires VM recreation (platform, disk, preset)


@dataclass
class VMSpec:
    """Normalized VM specification for comparison."""
    platform: str
    preset: t.Optional[str]
    cores: t.Optional[int]
    memory_gb: t.Optional[int]
    disk_boot_image: str
    disk_type: str
    disk_gb: int
    disk_block_bytes: int
    num_nics: int
    
    @classmethod
    def from_config(cls, vm_spec: dict) -> VMSpec:
        """Extract VMSpec from YAML vm_spec dict."""
        return cls(
            platform=vm_spec.get("platform") or "cpu-d3",
            preset=vm_spec.get("preset"),
            cores=vm_spec.get("cores"),
            memory_gb=vm_spec.get("memory_gb"),
            disk_boot_image=vm_spec.get("disk_boot_image") or vm_spec.get("image_family") or "ubuntu24.04-driverless",
            disk_type=vm_spec.get("disk_type", "network_ssd").upper(),
            disk_gb=vm_spec.get("disk_gb", 200),
            disk_block_bytes=vm_spec.get("disk_block_bytes", 4096),
            num_nics=int(vm_spec.get("num_nics", 1)),
        )
    
    @classmethod
    def from_live_vm(cls, vm_obj: t.Any, disk_obj: t.Any) -> VMSpec:
        """Extract VMSpec from live Nebius VM and disk objects.
        
        Args:
            vm_obj: Live VM instance object from Nebius API
            disk_obj: Live boot disk object from Nebius API
        
        Returns:
            VMSpec normalized from live resources
        """
        # Extract platform and preset from VM resources
        resources = getattr(getattr(vm_obj, "spec", None), "resources", None)
        platform = getattr(resources, "platform", "cpu-d3")
        preset = getattr(resources, "preset", None)
        cores = getattr(resources, "cores", None)
        memory_gb = getattr(resources, "memory_gb", None)
        
        # Extract disk specs
        disk_spec = getattr(disk_obj, "spec", None)
        disk_type = getattr(disk_spec, "type", "NETWORK_SSD")
        disk_gb = getattr(disk_spec, "size_gibibytes", 200)
        disk_block_bytes = getattr(disk_spec, "block_size_bytes", 4096)
        
        # Extract source image (if available in disk metadata)
        # Note: This may not always be available after disk creation
        source_image_id = getattr(disk_spec, "source_image_id", None)
        disk_boot_image = source_image_id or "ubuntu24.04-driverless"
        
        # Count NICs
        network_interfaces = getattr(getattr(vm_obj, "spec", None), "network_interfaces", [])
        num_nics = len(network_interfaces) if network_interfaces else 1
        
        return cls(
            platform=platform,
            preset=preset,
            cores=cores,
            memory_gb=memory_gb,
            disk_boot_image=disk_boot_image,
            disk_type=disk_type,
            disk_gb=disk_gb,
            disk_block_bytes=disk_block_bytes,
            num_nics=num_nics,
        )


@dataclass
class VMDiff:
    """Comparison result between desired and actual VM state."""
    change_type: ChangeType
    differences: t.List[str]  # Human-readable list of changes
    destructive_fields: t.List[str]  # Fields requiring recreation
    
    def has_changes(self) -> bool:
        """Returns True if any changes detected."""
        return self.change_type != ChangeType.NO_CHANGE
    
    def requires_recreation(self) -> bool:
        """Returns True if changes require VM recreation."""
        return self.change_type == ChangeType.DESTRUCTIVE
    
    def format_warning(self) -> str:
        """Format a user-friendly change summary (without recreation warning - CLI handles that)."""
        if not self.has_changes():
            return "No changes detected."
        
        if self.requires_recreation():
            lines = ["Destructive changes detected:"]
            for field in self.destructive_fields:
                lines.append(f"  • {field}")
            return "\n".join(lines)
        else:
            lines = ["Safe configuration changes detected:"]
            for diff in self.differences:
                lines.append(f"  • {diff}")
            lines.append("")
            lines.append("These will be applied via SSH config push (no VM recreation needed).")
            return "\n".join(lines)


class VMDiffAnalyzer:
    """Analyzes differences between desired and actual VM state."""
    
    # Fields that require VM recreation if changed
    # Note: num_nics is NOT in this list - expanding NICs is safe (attach operation)
    # Only shrinking NICs would be destructive (handled separately in compare())
    DESTRUCTIVE_FIELDS = {
        "platform",
        "preset",
        "disk_boot_image",
        "disk_type",
        "disk_block_bytes",
    }
    
    def compare(self, desired: VMSpec, actual: t.Optional[VMSpec]) -> VMDiff:
        """Compare desired vs actual VM specifications.
        
        Args:
            desired: VMSpec from YAML configuration
            actual: VMSpec from live VM, or None if VM doesn't exist
        
        Returns:
            VMDiff describing the changes
        """
        if actual is None:
            # VM doesn't exist - this is a safe creation, not a destructive change
            # No --recreate-gw flag needed for initial creation
            return VMDiff(
                change_type=ChangeType.SAFE,
                differences=["VM does not exist (will create)"],
                destructive_fields=[],
            )
        
        differences: t.List[str] = []
        destructive_fields: t.List[str] = []
        
        # Check platform
        if desired.platform != actual.platform:
            differences.append(f"platform: {actual.platform} → {desired.platform}")
            destructive_fields.append(f"platform ({actual.platform} → {desired.platform})")
        
        # Check preset (only if both use preset-based sizing)
        if desired.preset and actual.preset:
            if desired.preset != actual.preset:
                differences.append(f"preset: {actual.preset} → {desired.preset}")
                destructive_fields.append(f"preset ({actual.preset} → {desired.preset})")
        elif desired.preset and not actual.preset:
            # Switching from cores/memory to preset (safe if resources match)
            differences.append(f"switching to preset: {desired.preset}")
        elif not desired.preset and actual.preset:
            # Switching from preset to cores/memory
            differences.append(f"switching from preset {actual.preset} to explicit cores/memory")
        
        # Check disk image (comparing is tricky since live disk may only have image ID)
        # We'll flag any mismatch as potentially destructive
        if desired.disk_boot_image != actual.disk_boot_image:
            # Only flag if it's not an ID comparison issue
            if not (desired.disk_boot_image.startswith("computeimage-") or 
                   actual.disk_boot_image.startswith("computeimage-")):
                differences.append(f"disk_boot_image: {actual.disk_boot_image} → {desired.disk_boot_image}")
                destructive_fields.append(f"disk_boot_image ({actual.disk_boot_image} → {desired.disk_boot_image})")
        
        # Check disk type
        desired_dt = self._normalize_disk_type(desired.disk_type)
        actual_dt = self._normalize_disk_type(actual.disk_type)
        if desired_dt != actual_dt:
            differences.append(f"disk_type: {actual_dt} → {desired_dt}")
            destructive_fields.append(f"disk_type ({actual_dt} → {desired_dt})")
        
        # Check disk size (shrinking requires recreation, expanding is safe)
        if desired.disk_gb != actual.disk_gb:
            if desired.disk_gb < actual.disk_gb:
                differences.append(f"disk_gb: {actual.disk_gb} → {desired.disk_gb} (SHRINKING)")
                destructive_fields.append(f"disk_gb shrink ({actual.disk_gb}GB → {desired.disk_gb}GB)")
            else:
                differences.append(f"disk_gb: {actual.disk_gb} → {desired.disk_gb} (expanding - safe)")
        
        # Check disk block size
        if desired.disk_block_bytes != actual.disk_block_bytes:
            differences.append(f"disk_block_bytes: {actual.disk_block_bytes} → {desired.disk_block_bytes}")
            destructive_fields.append(f"disk_block_bytes ({actual.disk_block_bytes} → {desired.disk_block_bytes})")
        
        # Check num_nics (expanding is safe, shrinking is destructive)
        if desired.num_nics != actual.num_nics:
            if desired.num_nics > actual.num_nics:
                differences.append(f"num_nics: {actual.num_nics} → {desired.num_nics} (expanding - safe)")
            else:
                differences.append(f"num_nics: {actual.num_nics} → {desired.num_nics} (SHRINKING)")
                destructive_fields.append(f"num_nics shrink ({actual.num_nics} → {desired.num_nics})")
        
        # Determine change type
        if not differences:
            return VMDiff(
                change_type=ChangeType.NO_CHANGE,
                differences=[],
                destructive_fields=[],
            )
        
        if destructive_fields:
            return VMDiff(
                change_type=ChangeType.DESTRUCTIVE,
                differences=differences,
                destructive_fields=destructive_fields,
            )
        
        return VMDiff(
            change_type=ChangeType.SAFE,
            differences=differences,
            destructive_fields=[],
        )
    
    @staticmethod
    def _normalize_disk_type(disk_type: t.Union[str, int]) -> str:
        """Normalize disk type for comparison.
        
        Handles both string names and numeric enum values from Nebius API.
        """
        # Handle numeric enum values from Nebius API
        # Common mappings: 1 = NETWORK_SSD, 2 = NETWORK_HDD, 3 = NETWORK_SSD_IO_M3
        if isinstance(disk_type, int):
            disk_type_map = {
                1: "NETWORK_SSD",
                2: "NETWORK_HDD",
                3: "NETWORK_SSD_IO_M3",
                4: "NETWORK_SSD_NONREPLICATED",
            }
            return disk_type_map.get(disk_type, "NETWORK_SSD")
        
        # Handle string values
        dt = str(disk_type).upper()
        # Map aliases to canonical values
        if dt in {"SSD", "NVME"}:
            return "NETWORK_SSD"
        if dt == "HDD":
            return "NETWORK_HDD"
        return dt
