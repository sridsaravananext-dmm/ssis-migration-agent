"""Source-neutral migration interfaces layered over the legacy pipeline."""

from .contract_bridge import build_migration_contract, prepare_generation_step, validate_contract
from .normalized_plan import bind_plan_to_ir, validate_plan_ir_binding

__all__ = ["build_migration_contract", "prepare_generation_step", "validate_contract",
           "bind_plan_to_ir", "validate_plan_ir_binding"]
