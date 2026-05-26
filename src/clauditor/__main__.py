"""Enable ``python -m clauditor`` to dispatch to the CLI entry point."""

from clauditor.cli import main

raise SystemExit(main())
