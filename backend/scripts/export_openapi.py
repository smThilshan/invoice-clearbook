"""Write the API description (OpenAPI JSON) for the Workbench's generated types.

    make web-types    # this script, then openapi-typescript -> workbench/src/api/schema.d.ts

Built from the app object, so no server needs to run.
"""

import json
import sys
from pathlib import Path

from app.core.config import Settings
from app.main import create_app


def main() -> int:
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("openapi.json")
    schema = create_app(Settings(_env_file=None, app_env="dev")).openapi()
    target.write_text(json.dumps(schema, indent=2, sort_keys=True) + "\n")
    print(f"wrote {target} ({len(schema.get('paths', {}))} paths)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
