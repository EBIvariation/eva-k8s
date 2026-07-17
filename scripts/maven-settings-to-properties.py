#!/usr/bin/env python3
import argparse
import csv
import re
import sys
from pathlib import Path

from ebi_eva_internal_pyutils.config_utils import get_properties_from_xml_file
from lxml import etree as et

# Mapping format: { spring_property: source }
# source is either a Maven property name to look up, or '=<literal>' for a hard-coded value.
# Example: 'spring.jpa.hibernate.ddl-auto': '=update'

LITERAL_PREFIX = '='
TEMPLATE_PATTERN = re.compile(r'\|([^|]+)\|')

EVA_SEQCOL_MAPPING = {
    'spring.datasource.url':           'eva.evapro.jdbc.url',
    'spring.datasource.username':      'eva.evapro.user',
    'spring.datasource.password':      'eva.evapro.password',
    'spring.jpa.hibernate.ddl-auto':   '=update',
    'controller.auth.admin.username':  'seqcol.admin-user',
    'controller.auth.admin.password':  'seqcol.admin-password',
    'ftp.proxy.host':                  '=null',
    'ftp.proxy.port':                  '=0'
}

EVA_ACCESSION_WS_MAPPING = {
    'spring.data.mongodb.uri':                   'mongodb://|eva.mongo.user|:|eva.mongo.passwd.url-encoded|@|eva.mongo.host|/admin',
    'spring.data.mongodb.database':              'eva.accession.mongo.database',
    'mongodb.read-preference':                   'eva.mongo.read-preference',
    'human.mongodb.uri':                         'mongodb://|eva.mongo.user|:|eva.mongo.passwd.url-encoded|@|eva.mongo.host|/admin',
    'human.mongodb.database':                    'eva.accession.mongo.human.database',
    'continuous.id.blocks.datasource.jdbc-url':  'eva.accession.jdbc.url',
    'continuous.id.blocks.datasource.username':  'eva.accession.user',
    'continuous.id.blocks.datasource.password':  'eva.accession.password',
    'contig-alias.url':                          'contig-alias.url',
    'eva.api.base-url':                          'eva.api.base-url'
}

PROPERTY_SETS = {
    'eva-seqcol': EVA_SEQCOL_MAPPING,
    'eva-accession-ws': EVA_ACCESSION_WS_MAPPING
}


def load_csv_mapping(csv_file: str) -> dict:
    """
    Load property mapping from CSV file.
    CSV format: spring_property,source (with optional header row).
    'source' is either a Maven property name or '=<literal>' for a hard-coded value.
    Returns an ordered dict mapping spring properties to their sources.
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
            if len(rows[0]) == 2 and rows[0][0].lower() == 'spring_property':
                start_row = 1

            for i, row in enumerate(rows[start_row:], start=start_row + 1):
                if len(row) != 2:
                    print(f"Error: Invalid CSV format at line {i}: expected 2 columns, got {len(row)}")
                    return None
                spring_key, source = row[0].strip(), row[1].strip()
                if not spring_key or not source:
                    print(f"Error: Empty values in CSV at line {i}")
                    return None
                mapping[spring_key] = source

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

    missing_props = [
        prop for source in mapping.values()
        for prop in get_required_maven_props(source)
        if prop not in maven_props
    ]
    if missing_props:
        for prop in missing_props:
            print(f"Error: Required Maven property '{prop}' not found in profile '{profile}'")
        return False

    lines = []
    for spring_key, source in mapping.items():
        value = resolve_source_value(source, maven_props)
        lines.append(f"{spring_key}={value if value is not None else ''}")

    content = '\n'.join(lines) + '\n'

    try:
        Path(output_path).write_text(content)
        print(f"✓ Converted {maven_file}")
        print(f"  Profile: {profile}")
        print(f"  Output: {output_path}")
        return True
    except OSError as e:
        print(f"Error: Could not write to {output_path}: {e}")
        return False


def get_required_maven_props(source: str) -> list:
    if source.startswith(LITERAL_PREFIX):
        return []
    placeholders = TEMPLATE_PATTERN.findall(source)
    return placeholders if placeholders else [source]


def resolve_source_value(source: str, maven_props: dict):
    if source.startswith(LITERAL_PREFIX):
        return source[len(LITERAL_PREFIX):]

    if TEMPLATE_PATTERN.search(source):
        def _replace(match):
            value = maven_props[match.group(1)]
            return value if value is not None else ''

        return TEMPLATE_PATTERN.sub(_replace, source)

    return maven_props[source]


def main():
    parser = argparse.ArgumentParser(
        description="Convert Maven settings.xml profile to Spring Boot application.properties",
        epilog="Examples:\n  Preset: python scripts/maven-settings-to-properties.py --maven_file settings.xml --profile dev --property_set eva-seqcol\n  CSV: python scripts/maven-settings-to-properties.py --maven_file settings.xml --profile dev --mapping-csv mapping.csv"
    )
    parser.add_argument(
        "--maven_file", required=True, metavar="FILE", help="Path to Maven settings.xml file"
    )
    parser.add_argument(
        "--profile", required=True, metavar="PROFILE",
        help="Maven profile ID (e.g. development, production)"
    )
    parser.add_argument("--output", required=True, metavar="FILE",
                        help="Output file path (defaults to stdout)")

    mapping_group = parser.add_mutually_exclusive_group(required=True)
    mapping_group.add_argument(
        "--property_set",
        metavar="SET",
        help=f"Preset property set name ({', '.join(sorted(PROPERTY_SETS.keys()))})"
    )
    mapping_group.add_argument(
        "--mapping-csv",
        metavar="FILE",
        help="CSV file with property mappings (spring_property,source) where source is a Maven property name or '=<literal>'"
    )


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
