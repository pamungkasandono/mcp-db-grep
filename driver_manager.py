"""
Database Driver Manager for db-grep.

Provides a uniform, semantic registry for inspecting and managing database drivers:
- postgres: built-in psycopg (no external driver needed)
- mysql: built-in pymysql (no external driver needed)
- oracle: python-oracledb (Thin mode built-in; optional Instant Client for legacy 10g DBs)
"""

from pathlib import Path
import oracle_driver


DRIVER_SPECS = {
    "postgres": {
        "aliases": ["pgsql"],
        "name": "PostgreSQL",
        "type": "built-in",
        "description": "psycopg (pure-Python / bundled wheels)",
        "needs_external": False,
    },
    "mysql": {
        "aliases": ["my"],
        "name": "MySQL / MariaDB",
        "type": "built-in",
        "description": "pymysql (pure-Python)",
        "needs_external": False,
    },
    "oracle": {
        "aliases": ["ora"],
        "name": "Oracle Database",
        "type": "hybrid",
        "description": "python-oracledb (Thin mode built-in; Thick mode via Instant Client)",
        "needs_external": True,
    },
}

SUPPORTED_DRIVERS = tuple(DRIVER_SPECS.keys())
DRIVER_ALIASES = {
    alias: canonical
    for canonical, spec in DRIVER_SPECS.items()
    for alias in spec["aliases"]
}
DRIVER_SHORTHANDS = {
    canonical: spec["aliases"][0]
    for canonical, spec in DRIVER_SPECS.items()
    if spec["aliases"]
}


def normalize_driver_name(name: str) -> str:
    low = (name or "").strip().lower()
    for canonical, spec in DRIVER_SPECS.items():
        if low == canonical or low in spec["aliases"]:
            return canonical
    return low


def is_driver_ready(driver: str) -> bool:
    canonical = normalize_driver_name(driver)
    if canonical not in DRIVER_SPECS:
        return False
    spec = DRIVER_SPECS[canonical]
    if not spec["needs_external"]:
        return True
    if canonical == "oracle":
        return oracle_driver.is_driver_installed()
    return True


def install_driver(driver: str) -> bool:
    canonical = normalize_driver_name(driver)
    if canonical == "oracle":
        try:
            oracle_driver.download_and_install_driver()
            oracle_driver.ensure_thick_mode()
            return True
        except Exception as e:
            print(f"✗ Driver installation failed: {e}")
            return False
    return True


def ensure_driver_env():
    """Bootstrap OS library environment for external drivers before process initialization."""
    oracle_driver.ensure_oracle_env()


def get_driver_status(driver: str) -> dict:
    canonical = normalize_driver_name(driver)
    if canonical not in DRIVER_SPECS:
        return {"driver": driver, "known": False}

    spec = DRIVER_SPECS[canonical]
    if not spec["needs_external"]:
        return {
            "driver": canonical,
            "name": spec["name"],
            "known": True,
            "ready": True,
            "needs_external": False,
            "details": f"Built-in ready ({spec['description']} - no external driver needed)",
        }

    # Oracle
    if canonical == "oracle":
        installed = oracle_driver.is_driver_installed()
        client_dir = oracle_driver.find_oracle_client_dir()
        if installed and client_dir:
            return {
                "driver": canonical,
                "name": spec["name"],
                "known": True,
                "ready": True,
                "needs_external": True,
                "installed": True,
                "path": str(client_dir),
                "details": f"Instant Client installed ({client_dir})",
            }
        else:
            return {
                "driver": canonical,
                "name": spec["name"],
                "known": True,
                "ready": True,  # thin mode is ready
                "needs_external": True,
                "installed": False,
                "details": "Thin mode active (Default). For legacy 10g DBs, run: db-grep --setup-driver oracle",
            }

    return {"driver": driver, "known": False}


def print_all_driver_statuses():
    print("\n--- db-grep Database Driver Status ---")
    for key in DRIVER_SPECS:
        st = get_driver_status(key)
        name = st.get("name", key)
        prefix = "✓" if (not st.get("needs_external") or st.get("installed")) else "○"
        print(f"  {prefix} {key:<10} [{name}]: {st['details']}")
    print()


def setup_driver(driver_arg: str | None = None) -> bool:
    """Entry point for db-grep --setup-driver [DRIVER]. Returns True on success, False on error."""
    if not driver_arg or driver_arg in ("status", "list", "all"):
        print_all_driver_statuses()
        return True

    canonical = normalize_driver_name(driver_arg)
    if canonical not in DRIVER_SPECS:
        print(f"Error: Unknown driver '{driver_arg}'.")
        print(f"Supported drivers: {', '.join(DRIVER_SPECS.keys())}")
        return False

    spec = DRIVER_SPECS[canonical]
    if not spec["needs_external"]:
        print(f"\n[{canonical}] {spec['name']}")
        print(f"  Status: Built-in ({spec['description']})")
        print("  Notice: No external driver needed. It works out-of-the-box.\n")
        return True

    if canonical == "oracle":
        return oracle_driver.setup_oracle_driver_cli()

    return True
