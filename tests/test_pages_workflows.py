"""Static checks for the small Pages workflow sections, using only stdlib."""
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ('report.yml', 'dashboard.yml')
ARTIFACT_NAME = 'github-pages-${{ github.run_id }}-${{ github.run_attempt }}'


def action_block(text, action):
    """Locate one uses step without reading values from adjacent named steps."""
    lines = text.splitlines()
    pattern = re.compile(r'^\s*(?:-\s+)?uses:\s*' + re.escape(action) + r'@\S+\s*$')
    matches = [index for index, line in enumerate(lines) if pattern.match(line)]
    if len(matches) != 1:
        raise AssertionError(f'Expected one {action} step, found {len(matches)}')
    index = matches[0]
    line = lines[index]
    step_indent = len(line) - len(line.lstrip())
    if not line.lstrip().startswith('- '):
        step_indent -= 2  # uses follows a - name entry in the same step.
    end = index + 1
    while end < len(lines):
        candidate = lines[end]
        if candidate.strip() and not candidate.lstrip().startswith('#'):
            indent = len(candidate) - len(candidate.lstrip())
            if indent <= step_indent:
                break
        end += 1
    return '\n'.join(lines[index:end])


def child_value(text, parent, child):
    """Read an immediate mapping value from these block-style workflow files."""
    lines = text.splitlines()
    headers = [index for index, line in enumerate(lines) if line.strip() == parent + ':']
    if len(headers) != 1:
        raise AssertionError(f'Expected one {parent} mapping, found {len(headers)}')
    index = headers[0]
    indent = len(lines[index]) - len(lines[index].lstrip())
    matches = []
    for line in lines[index + 1:]:
        if not line.strip() or line.lstrip().startswith('#'):
            continue
        child_indent = len(line) - len(line.lstrip())
        if child_indent <= indent:
            break
        if child_indent == indent + 2 and line.strip().startswith(child + ':'):
            value = line.strip().split(':', 1)[1].strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
                value = value[1:-1]
            matches.append(value)
    if len(matches) != 1:
        raise AssertionError(f'Expected one {parent}.{child}, found {len(matches)}')
    return matches[0]


class PagesWorkflowTests(unittest.TestCase):
    def workflows(self):
        for name in WORKFLOWS:
            yield name, (ROOT / '.github' / 'workflows' / name).read_text(encoding='utf-8')

    def test_upload_and_deploy_use_the_same_run_and_attempt_scoped_artifact(self):
        for name, text in self.workflows():
            with self.subTest(workflow=name):
                upload = child_value(action_block(text, 'actions/upload-pages-artifact'), 'with', 'name')
                deploy = child_value(action_block(text, 'actions/deploy-pages'), 'with', 'artifact_name')
                self.assertEqual(upload, ARTIFACT_NAME)
                self.assertEqual(deploy, upload)

    def test_both_workflows_share_a_non_cancelling_publication_lock(self):
        for name, text in self.workflows():
            with self.subTest(workflow=name):
                self.assertEqual(child_value(text, 'concurrency', 'group'), 'market-report-publication')
                self.assertEqual(child_value(text, 'concurrency', 'cancel-in-progress'), 'false')

    def test_checkout_reads_main_after_acquiring_the_publication_lock(self):
        for name, text in self.workflows():
            with self.subTest(workflow=name):
                self.assertEqual(child_value(action_block(text, 'actions/checkout'), 'with', 'ref'), 'main')
                # The lock remains workflow-level, so checkout happens after waiting.
                self.assertRegex(text, r'(?m)^concurrency:\s*$')


if __name__ == '__main__':
    unittest.main()
