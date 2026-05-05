#!/usr/bin/env python3
"""
Lightweight Home Assistant YAML validator for CI/CD pipelines.

Validates HA YAML files (automations.yaml, scripts.yaml, scenes.yaml,
binary_sensors.yaml, dashboards/*.yaml) for common structural errors
that yamllint won't catch.

Usage:
    python3 scripts/validate-ha-yaml.py <path-to-config-dir>

Exit codes:
    0 - All files valid
    1 - Validation errors found
"""

import sys
import os
import yaml


def load_yaml_file(path):
    """Load and parse a YAML file."""
    try:
        with open(path, 'r') as f:
            return yaml.safe_load(f), None
    except yaml.YAMLError as e:
        return None, f"YAML parse error in {path}: {e}"
    except FileNotFoundError:
        return None, f"File not found: {path}"


def validate_automations(data, path):
    """Validate automations.yaml structure."""
    errors = []
    if data is None:
        return errors  # Empty file, will be handled by init container

    if not isinstance(data, list):
        errors.append(f"{path}: automations.yaml must be a list of automation objects, got {type(data).__name__}")
        return errors

    for i, item in enumerate(data):
        if not isinstance(item, dict):
            errors.append(f"{path}[{i}]: automation entry must be a dict, got {type(item).__name__}")
            continue

        # Check for common mistake: wrapping in 'automation:' key
        if 'automation' in item and len(item) == 1:
            errors.append(
                f"{path}[{i}]: 'automation:' key found at top level. "
                "Since configuration.yaml already has 'automation: !include automations.yaml', "
                "the included file should be a bare list — remove the 'automation:' wrapper."
            )

        # Validate required fields
        if 'trigger' not in item and 'alias' not in item:
            errors.append(f"{path}[{i}]: automation entry missing 'trigger' and 'alias'")

        if 'trigger' in item:
            if not isinstance(item['trigger'], (list, dict)):
                errors.append(f"{path}[{i}]: 'trigger' must be a list or dict")

        if 'action' in item:
            if not isinstance(item['action'], (list, dict)):
                errors.append(f"{path}[{i}]: 'action' must be a list or dict")

    return errors


def validate_scripts(data, path):
    """Validate scripts.yaml structure."""
    errors = []
    if data is None:
        return errors

    if not isinstance(data, dict):
        errors.append(f"{path}: scripts.yaml must be a dict of script definitions, got {type(data).__name__}")
        return errors

    for name, script in data.items():
        if not isinstance(script, (list, dict)):
            errors.append(f"{path}['{name}']: script must be a list or dict, got {type(script).__name__}")

    return errors


def validate_scenes(data, path):
    """Validate scenes.yaml structure."""
    errors = []
    if data is None:
        return errors

    if not isinstance(data, list):
        errors.append(f"{path}: scenes.yaml must be a list of scene objects, got {type(data).__name__}")
        return errors

    for i, scene in enumerate(data):
        if not isinstance(scene, dict):
            errors.append(f"{path}[{i}]: scene entry must be a dict")
            continue
        if 'name' not in scene:
            errors.append(f"{path}[{i}]: scene entry missing 'name'")

    return errors


def validate_binary_sensors(data, path):
    """Validate binary_sensors.yaml structure."""
    errors = []
    if data is None:
        return errors

    if not isinstance(data, list):
        errors.append(f"{path}: binary_sensors.yaml must be a list, got {type(data).__name__}")
        return errors

    for i, sensor in enumerate(data):
        if not isinstance(sensor, dict):
            errors.append(f"{path}[{i}]: binary_sensor entry must be a dict")
            continue
        if 'platform' not in sensor:
            errors.append(f"{path}[{i}]: binary_sensor entry missing 'platform'")

    return errors


def validate_dashboard(data, path):
    """Validate dashboard YAML structure."""
    errors = []
    if data is None:
        errors.append(f"{path}: dashboard YAML is empty")
        return errors

    if not isinstance(data, dict):
        errors.append(f"{path}: dashboard must be a dict with 'title' and 'views', got {type(data).__name__}")
        return errors

    if 'title' not in data:
        errors.append(f"{path}: dashboard missing required 'title' field")

    if 'views' not in data:
        errors.append(f"{path}: dashboard missing required 'views' field")
    elif not isinstance(data['views'], list):
        errors.append(f"{path}: 'views' must be a list")
    else:
        for i, view in enumerate(data['views']):
            if not isinstance(view, dict):
                errors.append(f"{path}.views[{i}]: view must be a dict")
                continue
            if 'title' not in view:
                errors.append(f"{path}.views[{i}]: view missing 'title'")
            if 'cards' in view:
                if not isinstance(view['cards'], list):
                    errors.append(f"{path}.views[{i}].cards: must be a list")
                else:
                    for j, card in enumerate(view['cards']):
                        if not isinstance(card, dict):
                            errors.append(f"{path}.views[{i}].cards[{j}]: card must be a dict")
                            continue
                        if 'type' not in card:
                            errors.append(f"{path}.views[{i}].cards[{j}]: card missing 'type'")

    return errors


def validate_file(path, expected_type):
    """Validate a single file."""
    data, error = load_yaml_file(path)
    if error:
        return [error]

    validators = {
        'automations': validate_automations,
        'scripts': validate_scripts,
        'scenes': validate_scenes,
        'binary_sensors': validate_binary_sensors,
        'dashboard': validate_dashboard,
    }

    validator = validators.get(expected_type)
    if validator:
        return validator(data, path)
    return []


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 validate-ha-yaml.py <config-dir>")
        sys.exit(1)

    config_dir = sys.argv[1]
    all_errors = []

    # Validate top-level config files
    files_to_check = [
        ('automations.yaml', 'automations'),
        ('scripts.yaml', 'scripts'),
        ('scenes.yaml', 'scenes'),
        ('binary_sensors.yaml', 'binary_sensors'),
    ]

    for filename, ftype in files_to_check:
        path = os.path.join(config_dir, filename)
        if os.path.exists(path):
            all_errors.extend(validate_file(path, ftype))

    # Validate dashboard files
    dashboards_dir = os.path.join(config_dir, 'dashboards')
    if os.path.isdir(dashboards_dir):
        for fname in sorted(os.listdir(dashboards_dir)):
            if fname.endswith('.yaml') or fname.endswith('.yml'):
                path = os.path.join(dashboards_dir, fname)
                all_errors.extend(validate_file(path, 'dashboard'))

    if all_errors:
        print("HA YAML validation FAILED:")
        for error in all_errors:
            print(f"  ✗ {error}")
        sys.exit(1)
    else:
        print("HA YAML validation passed")
        sys.exit(0)


if __name__ == '__main__':
    main()
