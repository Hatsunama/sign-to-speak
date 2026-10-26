#!/usr/bin/env python3
"""Audit Cargo license declarations for this bounded component probe.

This selects a declared route for investigation. It does not inspect license
texts, target-specific dependencies, or grant redistribution clearance.
"""

import argparse
import json
import pathlib
import re
import sys

ALLOWED = frozenset({
    'MIT', 'Apache-2.0', 'BSD-2-Clause', 'BSD-3-Clause', 'ISC', 'Zlib',
    'Unicode-3.0', 'Unicode-DFS-2016', 'CC0-1.0', 'Unlicense', 'MPL-2.0',
})
# Cargo's historical slash convention means OR. Bound this probe to the
# declarations actually observed, so other slash expressions need review.
# https://doc.rust-lang.org/stable/style-guide/cargo.html#metadata-conventions
LEGACY_SLASH_ALIASES = {
    'MIT/Apache-2.0': 'MIT OR Apache-2.0',
    'Unlicense/MIT': 'Unlicense OR MIT',
}
TOKEN = re.compile(r'[A-Za-z0-9][A-Za-z0-9.-]*|[()]')


class InvalidExpression(ValueError):
    pass


def tokenize(expression):
    if not isinstance(expression, str) or not expression.strip():
        raise InvalidExpression('missing license declaration')
    tokens = []
    position = 0
    while position < len(expression):
        if expression[position].isspace():
            position += 1
            continue
        match = TOKEN.match(expression, position)
        if not match:
            raise InvalidExpression(f'invalid character at position {position}')
        tokens.append(match.group())
        position = match.end()
    return tokens


def parse(expression):
    tokens = tokenize(expression)
    position = 0

    def take(expected=None):
        nonlocal position
        if position >= len(tokens):
            raise InvalidExpression('incomplete expression')
        token = tokens[position]
        if expected is not None and token != expected:
            raise InvalidExpression(f'expected {expected}, found {token}')
        position += 1
        return token

    def identifier():
        token = take()
        if token in {'AND', 'OR', 'WITH', '(', ')'}:
            raise InvalidExpression(f'expected identifier, found {token}')
        return token

    def primary():
        if position < len(tokens) and tokens[position] == '(':
            take('(')
            value = alternative()
            take(')')
            return value
        license_id = identifier()
        if position < len(tokens) and tokens[position] == 'WITH':
            take('WITH')
            return ('with', license_id, identifier())
        return ('id', license_id)

    def conjunction():
        nodes = [primary()]
        while position < len(tokens) and tokens[position] == 'AND':
            take('AND')
            nodes.append(primary())
        return nodes[0] if len(nodes) == 1 else ('and', nodes)

    def alternative():
        nodes = [conjunction()]
        while position < len(tokens) and tokens[position] == 'OR':
            take('OR')
            nodes.append(conjunction())
        return nodes[0] if len(nodes) == 1 else ('or', nodes)

    tree = alternative()
    if position != len(tokens):
        raise InvalidExpression(f'unexpected token {tokens[position]}')
    return tree


def route(tree):
    kind = tree[0]
    if kind == 'id':
        return tree[1] if tree[1] in ALLOWED else None
    if kind == 'with':
        # No exceptions have been reviewed for this probe.
        return None
    if kind == 'and':
        parts = [route(child) for child in tree[1]]
        return ' AND '.join(f'({part})' if ' OR ' in part else part for part in parts) if all(parts) else None
    if kind == 'or':
        for child in tree[1]:
            selected = route(child)
            if selected:
                return selected
        return None
    raise AssertionError(kind)


def audit(metadata):
    packages = metadata.get('packages')
    if not isinstance(packages, list) or not packages:
        raise ValueError('Cargo metadata has no packages')
    entries = []
    for package in packages:
        name, version = package['name'], package['version']
        declaration = package.get('license')
        legacy_slash_alias = isinstance(declaration, str) and declaration in LEGACY_SLASH_ALIASES
        normalized = LEGACY_SLASH_ALIASES[declaration] if legacy_slash_alias else declaration
        try:
            selected = route(parse(normalized))
            reason = None if selected else 'no approved route in declaration'
        except InvalidExpression as error:
            selected = None
            reason = str(error)
        entries.append({
            'name': name, 'version': version,
            'license': declaration if declaration else 'not declared',
            'normalized_expression': normalized,
            'legacy_slash_alias': legacy_slash_alias,
            'allowed_for_probe': bool(selected),
            'selected_route': selected,
            'pending_review': reason,
        })
    entries.sort(key=lambda item: (item['name'], item['version']))
    blocked = [f"{item['name']} {item['version']}: {item['license']} ({item['pending_review']})"
               for item in entries if not item['allowed_for_probe']]
    return {
        'status': 'declarations-selected-for-component-probe' if not blocked else 'declarations-pending-review',
        'scope': 'Declaration audit only; full license texts and actual target dependencies remain separate release gates. No redistribution clearance.',
        'packages': entries,
        'blocked_packages': blocked,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--metadata', required=True, type=pathlib.Path)
    parser.add_argument('--output', required=True, type=pathlib.Path)
    args = parser.parse_args()
    report = audit(json.loads(args.metadata.read_text()))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + '\n')
    if report['blocked_packages']:
        for item in report['blocked_packages']:
            print(f'License declaration pending review: {item}', file=sys.stderr)
        return 1
    print(f"Selected declaration routes for {len(report['packages'])} Cargo packages; not redistribution clearance")
    return 0


if __name__ == '__main__':
    sys.exit(main())
