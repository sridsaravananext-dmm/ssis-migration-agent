from __future__ import annotations

import argparse
import json
from pathlib import Path

from ssis_migration.agent.orchestrator import AIMigrationOrchestrator
from ssis_migration.agent.provider_factory import create_provider


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="SSIS to Databricks AI migration agent"
    )

    parser.add_argument(
        "--provider",
        choices=["mock", "openai", "puter"],
        default="mock",
        help="AI provider to use.",
    )

    parser.add_argument(
        "--migration-step",
        required=True,
        help="Path to a migration-step JSON file.",
    )

    parser.add_argument(
        "--output",
        help="Optional output JSON path.",
    )

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    migration_step_path = Path(args.migration_step)

    if not migration_step_path.exists():
        parser.error(
            f"Migration step file not found: {migration_step_path}"
        )

    migration_step = json.loads(
        migration_step_path.read_text(
            encoding="utf-8"
        )
    )

    provider = create_provider(args.provider)

    orchestrator = AIMigrationOrchestrator(
        provider=provider
    )

    result = orchestrator.run(
        migration_step
    )

    result_dict = result.to_dict()

    if args.output:
        output_path = Path(args.output)

        output_path.parent.mkdir(
            parents=True,
            exist_ok=True
        )

        output_path.write_text(
            json.dumps(
                result_dict,
                indent=2,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        print(
            f"Migration result written to: {output_path}"
        )

    else:
        print(
            json.dumps(
                result_dict,
                indent=2,
                ensure_ascii=False,
            )
        )


if __name__ == "__main__":
    main()