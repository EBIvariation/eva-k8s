#!/usr/bin/env python3

import sys
import argparse
import csv
from pathlib import Path

try:
    from lxml import etree as et
except ImportError:
    print("Error: lxml is required but not installed")
    sys.exit(1)

from ebi_eva_internal_pyutils.config_utils import get_properties_from_xml_file


EVA_SEQCOL_MAPPING = {
    'app.db.url':            'spring.datasource.url',
    'app.db.username':       'spring.datasource.username',
    'app.db.password':       'spring.datasource.password',
    'app.ddl.behaviour':     'spring.jpa.hibernate.ddl-auto',
    'app.admin.username':    'controller.auth.admin.username',
    'app.admin.password':    'controller.auth.admin.password',
    'app.ftp.proxy.host':    'ftp.proxy.host',
    'app.ftp.proxy.port':    'ftp.proxy.port',
    'app.scaffolds.enabled': 'config.scaffolds.enabled',
}

PROPERTY_SETS = {
    'eva-seqcol': EVA_SEQCOL_MAPPING,
}


def load_csv_mapping(csv_file: str) -> dict:
    """
    Load property mapping from CSV file.
    CSV format: maven_property,spring_property (with optional header row).
    Returns ordered dict mapping maven properties to spring properties.
    """
    mapping = {}
    try:
        with open(csv_file, 'r') as f:
            reader = csv.reader(f)
            rows = list(reader)
            if not rows:
                print(f"Error: CSV file is empty: {csv_file}")
                return None

            start_row = 0
            if len(rows[0]) == 2 and rows[0][0].lower() == 'maven_property':
                start_row = 1

            for i, row in enumerate(rows[start_row:], start=start_row + 1):
                if len(row) != 2:
                    print(f"Error: Invalid CSV format at line {i}: expected 2 columns, got {len(row)}")
                    return None
                maven_key, spring_key = row[0].strip(), row[1].strip()
                if not maven_key or not spring_key:
                    print(f"Error: Empty property names in CSV at line {i}")
                    return None
                mapping[maven_key] = spring_key

        if not mapping:
            print(f"Error: No property mappings found in {csv_file}")
            return None
        return mapping
    except OSError as e:
        print(f"Error: Could not read CSV file {csv_file}: {e}")
        return None


def convert_maven_to_properties(maven_file: str, profile: str, mapping: dict, output_path: str = None) -> bool:
    """
    Convert Maven settings XML to Spring Boot application.properties format.
    Returns True if conversion was successful, False otherwise.
    """
    file = Path(maven_file)
    if not file.exists():
        print(f"Error: Maven settings file not found: {maven_file}")
        return False

    try:
        maven_props = get_properties_from_xml_file(profile, maven_file)
    except et.XMLSyntaxError as e:
        print(f"Error: Invalid XML in {maven_file}: {e}")
        return False
    except OSError as e:
        print(f"Error: Could not read {maven_file}: {e}")
        return False

    if maven_props is None:
        print(f"Error: Profile '{profile}' not found in {maven_file}")
        return False

    missing_props = []
    for maven_key in mapping.keys():
        if maven_key not in maven_props:
            missing_props.append(maven_key)

    if missing_props:
        for prop in missing_props:
            print(f"Error: Required Maven property '{prop}' not found in profile '{profile}'")
        return False

    lines = []
    for maven_key, spring_key in mapping.items():
        value = maven_props[maven_key]
        lines.append(f"{spring_key}={value if value is not None else ''}")

    content = '\n'.join(lines) + '\n'

    try:
        if output_path:
            Path(output_path).write_text(content)
            print(f"✓ Converted {maven_file}")
            print(f"  Profile: {profile}")
            print(f"  Output: {output_path}")
        else:
            sys.stdout.write(content)
        return True
    except OSError as e:
        print(f"Error: Could not write to {output_path}: {e}")
        return False


def main():
    parser = argparse.ArgumentParser(
        description="Convert Maven settings.xml profile to Spring Boot application.properties",
        epilog="Examples:\n  Preset: python scripts/maven-settings-to-properties.py --maven_file settings.xml --profile dev --property_set eva-seqcol\n  CSV: python scripts/maven-settings-to-properties.py --maven_file settings.xml --profile dev --mapping-csv mapping.csv"
    )
    parser.add_argument(
        "--maven_file", required=True, metavar="FILE", help="Path to Maven settings.xml file"
    )
    parser.add_argument(
        "--profile", required=True, metavar="PROFILE", help="Maven profile ID (e.g. development, production)"
    )

    mapping_group = parser.add_mutually_exclusive_group(required=True)
    mapping_group.add_argument(
        "--property_set",
        metavar="SET",
        help=f"Preset property set name ({', '.join(sorted(PROPERTY_SETS.keys()))})"
    )
    mapping_group.add_argument(
        "--mapping-csv",
        metavar="FILE",
        help="CSV file with property mappings (maven_property,spring_property)"
    )

    parser.add_argument("--output", metavar="FILE", help="Output file path (defaults to stdout)")

    args = parser.parse_args()

    if args.property_set:
        if args.property_set not in PROPERTY_SETS:
            known_sets = ', '.join(sorted(PROPERTY_SETS.keys()))
            print(f"Error: Unknown property set '{args.property_set}'. Known sets: {known_sets}")
            sys.exit(1)
        mapping = PROPERTY_SETS[args.property_set]
    else:
        mapping = load_csv_mapping(args.mapping_csv)
        if mapping is None:
            sys.exit(1)

    if convert_maven_to_properties(args.maven_file, args.profile, mapping, args.output):
        sys.exit(0)
    else:
        sys.exit(1)


if __name__ == "__main__":
    main()
