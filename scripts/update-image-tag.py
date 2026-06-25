#!/usr/bin/env python3

import sys
import argparse
import yaml
from pathlib import Path


def update_image_tag(file_path: str, new_tag: str) -> bool:
    """
    Update the newTag value in a kustomization.yaml file.
    """
    try:
        file = Path(file_path)
        if not file.exists():
            print(f"Error: File not found: {file_path}")

        # Read and parse YAML
        with open(file, 'r') as f:
            data = yaml.safe_load(f)

        if data is None:
            print(f"Error: Empty or invalid YAML file: {file_path}")

        # Check if images section exists
        if 'images' not in data:
            print(f"Error: No 'images' section found in {file_path}")

        images = data['images']
        if not isinstance(images, list) or len(images) == 0:
            print(f"Error: 'images' section is empty or not a list in {file_path}")

        # Update newTag in the first image (usually the only one)
        old_tag = images[0].get('newTag', 'unknown')
        images[0]['newTag'] = new_tag

        # Write back to file with proper formatting
        with open(file, 'w') as f:
            yaml.dump(data, f, default_flow_style=False, sort_keys=False)

        print(f"✓ Updated {file_path}")
        print(f"  {old_tag} → {new_tag}")

    except yaml.YAMLError as e:
        print(f"Error parsing YAML: {e}")
    except Exception as e:
        print(f"Error: {e}")


def main():
    parser = argparse.ArgumentParser(
        description="Update the newTag value in a kustomization.yaml file",
        epilog="Example: ./scripts/update-image-tag.py k8s-manifests/eva-seqcol/overlays/dev/kustomization.yaml v1.2.3"
    )
    parser.add_argument(
        "file",
        metavar="KUSTOMIZATION_FILE",
        help="Path to the kustomization.yaml file"
    )
    parser.add_argument(
        "tag",
        metavar="NEW_TAG",
        help="New image tag value (e.g., v1.2.3, dev-abc1234)"
    )

    args = parser.parse_args()

    if update_image_tag(args.file, args.tag):
        sys.exit(0)
    else:
        sys.exit(1)


if __name__ == "__main__":
    main()
